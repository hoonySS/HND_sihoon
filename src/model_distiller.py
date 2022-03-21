import argparse
import datetime
import sys
import time

import torch
import torchvision
from torch import distributed as dist
from torch.nn import DataParallel, SyncBatchNorm
from torch.nn.parallel import DistributedDataParallel

from myutils.common import file_util, yaml_util
from myutils.pytorch import func_util, module_util
from structure.logger import MetricLogger, SmoothedValue
from tools.distillation import DistillationBox
from utils import main_util, mimic_util, dataset_util

try:
    from apex import amp
except ImportError:
    amp = None


def get_argparser():
    parser = argparse.ArgumentParser(description='Knowledge distillation for image classification models')
    parser.add_argument('--config', required=True, help='yaml file path')
    parser.add_argument('--device', default='cuda', help='device')
    parser.add_argument('--start_epoch', default=0, type=int, metavar='N', help='start epoch')
    parser.add_argument('-sync_bn', action='store_true', help='Use sync batch norm')
    parser.add_argument('-test_only', action='store_true', help='Only test the models')
    parser.add_argument('-student_only', action='store_true', help='Test the student model only')
    # Mixed precision training parameters
    parser.add_argument('-apex', action='store_true',
                        help='Use apex for mixed precision training')
    parser.add_argument('--apex_opt_level', default='O1', type=str,
                        help='For apex mixed precision training'
                             'O0 for FP32 training, O1 for mixed precision training.'
                             'For further detail, see https://github.com/NVIDIA/apex/tree/master/examples/imagenet')
    # distributed training parameters
    parser.add_argument('--world_size', default=1, type=int, help='number of distributed processes')
    parser.add_argument('--dist_url', default='env://', help='url used to set up distributed training')
    return parser


def load_ckpt(ckpt_file_path, model=None, optimizer=None, lr_scheduler=None, strict=True):
    if not file_util.check_if_exists(ckpt_file_path):
        print('ckpt file is not found at `{}`'.format(ckpt_file_path))
        return None, None

    ckpt = torch.load(ckpt_file_path, map_location='cpu')
    if model is not None:
        print('Loading model parameters')
        model.load_state_dict(ckpt['model'], strict=strict)
    if optimizer is not None:
        print('Loading optimizer parameters')
        optimizer.load_state_dict(ckpt['optimizer'])
    if lr_scheduler is not None:
        print('Loading scheduler parameters')
        lr_scheduler.load_state_dict(ckpt['lr_scheduler'])
    return ckpt.get('best_value', 0.0), ckpt['config'], ckpt['args']


def get_model(model_config, device, distributed, sync_bn):
    model_name = model_config['type']
    model = torchvision.models.__dict__[model_name](**model_config['params'])
    if distributed and sync_bn:
        model = SyncBatchNorm.convert_sync_batchnorm(model)

    ckpt_file_path = model_config['ckpt']
    load_ckpt(ckpt_file_path, model=model, strict=True)
    return model.to(device)


def save_ckpt(model, optimizer, lr_scheduler, best_value, config, args, output_file_path):
    file_util.make_parent_dirs(output_file_path)
    model_state_dict =\
        model.module.state_dict() if isinstance(model, DistributedDataParallel) else model.state_dict()
    main_util.save_on_master({'model': model_state_dict, 'optimizer': optimizer.state_dict(), 'best_value': best_value,
                              'lr_scheduler': lr_scheduler.state_dict(), 'config': config, 'args': args},
                             output_file_path)


def distill_one_epoch(distillation_box, train_data_loader, optimizer, device, epoch, interval, use_apex=False):
    metric_logger = MetricLogger(delimiter='  ')
    metric_logger.add_meter('lr', SmoothedValue(window_size=1, fmt='{value}'))
    metric_logger.add_meter('img/s', SmoothedValue(window_size=10, fmt='{value}'))
    header = 'Epoch: [{}]'.format(epoch)
    for sample_batch, targets in metric_logger.log_every(train_data_loader, interval, header):
        start_time = time.time()
        sample_batch, targets = sample_batch.to(device), targets.to(device)
        loss = distillation_box(sample_batch, targets)
        optimizer.zero_grad()
        if use_apex:
            with amp.scale_loss(loss, optimizer) as scaled_loss:
                scaled_loss.backward()
        else:
            loss.backward()
        optimizer.step()

        batch_size = sample_batch.shape[0]
        metric_logger.update(loss=loss.item(), lr=optimizer.param_groups[0]['lr'])
        metric_logger.meters['img/s'].update(batch_size / (time.time() - start_time))


@torch.no_grad()
def evaluate(model, data_loader, device, interval=1000, split_name='Test', title=None):
    if title is not None:
        print(title)

    num_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    model.eval()
    metric_logger = MetricLogger(delimiter='  ')
    header = '{}:'.format(split_name)
    with torch.no_grad():
        for image, target in metric_logger.log_every(data_loader, interval, header):
            image = image.to(device, non_blocking=True)
            target = target.to(device, non_blocking=True)
            output = model(image)

            acc1, acc5 = main_util.compute_accuracy(output, target, topk=(1, 5))
            # FIXME need to take into account that the datasets
            # could have been padded in distributed setup
            batch_size = image.shape[0]
            metric_logger.meters['acc1'].update(acc1.item(), n=batch_size)
            metric_logger.meters['acc5'].update(acc5.item(), n=batch_size)

    # gather the stats from all processes
    metric_logger.synchronize_between_processes()
    top1_accuracy = metric_logger.acc1.global_avg
    top5_accuracy = metric_logger.acc5.global_avg
    print(' * Acc@1 {:.4f}\tAcc@5 {:.4f}\n'.format(top1_accuracy, top5_accuracy))
    torch.set_num_threads(num_threads)
    return metric_logger.acc1.global_avg


def distill(teacher_model, student_model, train_data_loader, val_data_loader, device,
            distributed, start_epoch, config, args):
    print('Start knowledge distillation')
    train_config = config['train']
    distillation_box = DistillationBox(teacher_model, student_model, train_config['criterion'])
    ckpt_file_path = config['mimic_model']['ckpt']
    optim_config = train_config['optimizer']
    optimizer = func_util.get_optimizer(student_model, optim_config['type'], optim_config['params'])
    scheduler_config = train_config['scheduler']
    lr_scheduler = func_util.get_scheduler(optimizer, scheduler_config['type'], scheduler_config['params'])
    best_val_top1_accuracy = 0.0
    if file_util.check_if_exists(ckpt_file_path):
        best_val_map, _, _ = load_ckpt(ckpt_file_path, optimizer=optimizer, lr_scheduler=lr_scheduler)

    interval = train_config['interval']
    if interval <= 0:
        num_batches = len(train_data_loader)
        interval = num_batches // 20 if num_batches >= 20 else 1

    student_model_without_ddp = \
        student_model.module if isinstance(student_model, DistributedDataParallel) else student_model
    start_time = time.time()
    for epoch in range(start_epoch, train_config['epoch']):
        if distributed:
            train_data_loader.sampler.set_epoch(epoch)

        teacher_model.eval()
        student_model.train()
        distill_one_epoch(distillation_box, train_data_loader, optimizer, device, epoch, interval, args.apex)
        val_top1_accuracy =\
            evaluate(student_model, val_data_loader, device=device, interval=interval, split_name='Validation')
        if val_top1_accuracy > best_val_top1_accuracy and main_util.is_main_process():
            print('Updating ckpt (Best top1 accuracy: {:.4f} -> {:.4f})'.format(best_val_top1_accuracy,
                                                                                val_top1_accuracy))
            best_val_top1_accuracy = val_top1_accuracy
            save_ckpt(student_model_without_ddp, optimizer, lr_scheduler,
                      best_val_top1_accuracy, config, args, ckpt_file_path)
        lr_scheduler.step()

    dist.barrier()
    total_time = time.time() - start_time
    total_time_str = str(datetime.timedelta(seconds=int(total_time)))
    print('Training time {}'.format(total_time_str))


def main(args):
    if args.apex:
        if sys.version_info < (3, 0):
            raise RuntimeError('Apex currently only supports Python 3. Aborting.')
        if amp is None:
            raise RuntimeError('Failed to import apex. Please install apex from https://www.github.com/nvidia/apex '
                               'to enable mixed-precision training.')

    distributed, device_ids = main_util.init_distributed_mode(args.world_size, args.dist_url)
    print(args)
    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = True

    config = yaml_util.load_yaml_file(args.config)
    device = torch.device(args.device if torch.cuda.is_available() else 'cpu')
    dataset_config = config['dataset']
    input_shape = config['input_shape']
    train_config = config['train']
    test_config = config['test']
    train_data_loader, val_data_loader, test_data_loader =\
        dataset_util.get_data_loaders(dataset_config, batch_size=train_config['batch_size'],
                                      rough_size=train_config['rough_size'], reshape_size=input_shape[1:3],
                                      jpeg_quality=-1, test_batch_size=test_config['batch_size'],
                                      distributed=distributed)

    teacher_model_config = config['teacher_model']
    teacher_model, teacher_model_type = mimic_util.get_org_model(teacher_model_config, device)
    module_util.freeze_module_params(teacher_model)

    student_model = mimic_util.get_mimic_model_easily(config, device)
    student_model_config = config['mimic_model']

    optim_config = train_config['optimizer']
    optimizer = func_util.get_optimizer(student_model, optim_config['type'], optim_config['params'])
    use_apex = args.apex
    if use_apex:
        student_model, optimizer = amp.initialize(student_model, optimizer, opt_level=args.apex_opt_level)

    if distributed:
        teacher_model = DataParallel(teacher_model, device_ids=device_ids)
        student_model = DistributedDataParallel(student_model, device_ids=device_ids)

    start_epoch = args.start_epoch
    if not args.test_only:
        distill(teacher_model, student_model, train_data_loader, val_data_loader, device,
                distributed, start_epoch, config, args)
        student_model_without_ddp =\
            student_model.module if isinstance(student_model, DistributedDataParallel) else student_model
        load_ckpt(student_model_config['ckpt'], model=student_model_without_ddp, strict=True)

    if not args.student_only:
        evaluate(teacher_model, test_data_loader, device, title='[Teacher: {}]'.format(teacher_model_type))
    evaluate(student_model, test_data_loader, device, title='[Student: {}]'.format(student_model_config['type']))


if __name__ == '__main__':
    argparser = get_argparser()
    main(argparser.parse_args())

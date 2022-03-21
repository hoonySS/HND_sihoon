import numpy as np
import torchvision.transforms.functional as functional
from PIL import Image
from torch.utils.data import Dataset


class RgbImageDataset(Dataset):
    def __init__(self, file_path, size, transform=None, delimiter='\t'):
        self.transform = transform
        self.size = size
        self.file_paths = list()
        self.labels = list()
        self.label_dict = dict()
        with open(file_path, 'r') as fp:
            for line in fp:
                elements = line.strip().split(delimiter)
                self.file_paths.append(elements[0])
                label_name = elements[1]
                if label_name not in self.label_dict:
                    self.label_dict[label_name] = len(self.label_dict)
                self.labels.append(self.label_dict[label_name])

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        file_path, target = self.file_paths[idx], self.labels[idx]
        img = Image.open(file_path)
        img = functional.resize(img, self.size, interpolation=2)
        if self.transform is not None:
            img = self.transform(img)
        return img, target

    def load_all_data(self):
        all_data = []
        for i in range(len(self.labels)):
            img, _ = self.__getitem__(i)
            all_data.append(img)

        all_data = np.concatenate(all_data)
        return all_data.reshape(len(self.labels), self.size[0], self.size[1], 3)

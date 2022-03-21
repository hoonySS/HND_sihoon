import pickle
import socket
import time
from datetime import datetime

NO_RESPONSE = (None, 'Nothing received')


def serialize(*args):
    return pickle.dumps(tuple(args))


def deserialize(pickle_dump):
    x = pickle.loads(pickle_dump)
    return x[0] if len(x) == 1 else x


class SocketClient(object):
    def __init__(self, server_address, port=8080, timeout_sec=30, buffer_size=4096, requires_preprocess=False):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(timeout_sec if isinstance(timeout_sec, int) else float(timeout_sec))
        client.connect((server_address, port))
        self.client = client
        self.server_address = server_address
        self.port = port
        self.buffer_size = buffer_size
        self.requires_preprocess = requires_preprocess

    def preprocess(self, *args, **kwargs):
        raise NotImplementedError

    def send(self, data, close_at_end=True):
        client_start_time = time.time()
        preprocessed_data = self.preprocess(data) if self.requires_preprocess else data
        try:
            if self.client.fileno() == -1:
                print('Reconnecting to {} ...'.format(self.server_address))
                self.client.connect((self.server_address, self.port))

            self.client.sendall(preprocessed_data)
        except socket.timeout:
            print('Timeout @ client in sending')

        if close_at_end:
            self.client.close()

        client_end_time = time.time()
        return client_start_time, client_end_time

    def receive(self, close_at_end=True):
        client_start_time = time.time()
        data_from_server = NO_RESPONSE
        try:
            if self.client.fileno() == -1:
                print('Reconnecting to {} using port: {}'.format(self.server_address, self.port))
                self.client.connect((self.server_address, self.port))

            while True:
                data_from_server = self.client.recv(self.buffer_size)
                if data_from_server:
                    break
        except socket.timeout:
            print('Timeout @ client in receiving')

        if close_at_end:
            self.client.close()

        client_end_time = time.time()
        return data_from_server, client_start_time, client_end_time

    def send_and_receive(self, data, close_at_end=True):
        start_time2send, end_time2send = self.send(data, close_at_end=False)
        data_from_server, start_time2receive, end_time2receive = self.receive(close_at_end=close_at_end)
        return data_from_server, start_time2send, end_time2send, start_time2receive, end_time2receive


class SocketServer(object):
    DATE_FORMAT = '%m/%d/%Y, %H:%M:%S'

    def __init__(self, server_address='0.0.0.0', port=8080, timeout_sec=30, buffer_size=4096, num_unaccepted_conns=1):
        # server_address='0.0.0.0' if you want the server to listen on all assigned IP addresses
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((server_address, port))
        server.listen(num_unaccepted_conns)
        server.settimeout(timeout_sec if isinstance(timeout_sec, int) else float(timeout_sec))
        self.server = server
        self.server_address = server_address
        self.port = port
        self.buffer_size = buffer_size
        self.requires_process = False

    def process(self, *args, **kwargs):
        raise NotImplementedError

    def receive(self, close_at_end=True):
        open_time = datetime.now()
        connection, address = self.server.accept()
        while True:
            processed_data = NO_RESPONSE
            start_time2process = None
            try:
                data_from_client = connection.recv(self.buffer_size)
                if data_from_client:
                    continue

                start_time2process = time.time()
                processed_data = self.process(data_from_client) if self.requires_process else data_from_client
            except socket.timeout:
                print('Timeout @ server in receiving')

            end_time2process = time.time() if start_time2process is not None else None
            yield processed_data, start_time2process, end_time2process
            if close_at_end:
                self.server.close()
                break

        close_time = datetime.now()
        print('Server was open from {} until {}'.format(open_time.strftime(self.DATE_FORMAT),
                                                        close_time.strftime(self.DATE_FORMAT)))

    def receive_and_send(self, close_at_end=True):
        open_time = datetime.now()
        connection, address = self.server.accept()
        while True:
            processed_data = NO_RESPONSE
            start_time2process = None
            try:
                data_from_client = connection.recv(self.buffer_size)
                if data_from_client:
                    continue

                start_time2process = time.time()
                processed_data = self.process(data_from_client) if self.requires_process else data_from_client
                try:
                    connection.sendall(processed_data)
                except socket.timeout:
                    print('Timeout @ server in sending')
            except socket.timeout:
                print('Timeout @ server in receiving')

            end_time2process = time.time() if start_time2process is not None else None
            yield processed_data, start_time2process, end_time2process
            if close_at_end:
                self.server.close()
                break

        close_time = datetime.now()
        print('Server was open from {} until {}'.format(open_time.strftime(self.DATE_FORMAT),
                                                        close_time.strftime(self.DATE_FORMAT)))

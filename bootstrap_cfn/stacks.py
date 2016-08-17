
import etcd
from contextlib import contextmanager

class Etcd(object):

    client = None

    def __init__(self, host):
        self.setup(host)

    @contextmanager
    def setup(self, host):
        if self.client == None:
            self.client = etcd.Client(host=host)
        yield self.client

    def update_record(self, record, suffix, host=None):
        with self.setup(host) as client:
            written = client.write("/stacks/{}".format(record), suffix)

    def lookup_record(self, record, host=None):
        stack_suffix = None
        with self.setup(host) as client:
            try:
                stack_suffix = client.get("/stacks/{}".format(record))
            except etcd.EtcdKeyNotFound as e:
                raise(e)
        return stack_suffix.value


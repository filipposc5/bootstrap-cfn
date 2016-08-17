#How to run (OSX)

* https://github.com/jplana/python-etcd
* etcd (from https://github.com/coreos/etcd/releases ):

```
curl -L https://github.com/coreos/etcd/releases/download/v3.0.0/etcd-v3.0.0-darwin-amd64.zip -o etcd-v3.0.0-darwin-amd64.zip
unzip etcd-v3.0.0-darwin-amd64.zip
cd etcd-v3.0.0-darwin-amd64
./etcd
```

Create /stacks using etcdctl and list them

```
./etcdctl mkdir stacks
./etcdctl ls stacks
```



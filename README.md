# Computer_Networks_Project

Python client-server file transfer project.

## Run server

```bash
python server.py --host 127.0.0.1 --port 9000 --storage-dir server_files
```

## Run client

List files:

```bash
python client.py --host 127.0.0.1 --port 9000 list
```

Download a file:

```bash
python client.py --host 127.0.0.1 --port 9000 get <filename> <destination_path_or_directory>
```

## Run tests

```bash
python -m unittest discover -s tests -v
```

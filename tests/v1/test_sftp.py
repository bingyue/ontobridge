"""Actual SSH/SFTP transport, pinned host identity and read-only remote paths."""

import os
import socket
from threading import Event, Thread

import paramiko
import pytest
from ontobridge.service import DomainError
from ontobridge.v1.connector_sdk import ConnectorContext
from ontobridge.v1.file_connectors import SFTPConnector


class Auth(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return (
            paramiko.AUTH_SUCCESSFUL if (username, password) == ("mock", "fixture") else paramiko.AUTH_FAILED
        )

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return (
            paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )


class Files(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, root, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.root = root

    def local(self, path):
        value = (self.root / path.lstrip("/")).resolve()
        if not value.is_relative_to(self.root):
            raise OSError("outside root")
        return value

    def canonicalize(self, path):
        if path.endswith("escape.csv"):
            return "/private/secret.csv"
        return path

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
        except OSError:
            return paramiko.SFTP_NO_SUCH_FILE

    def open(self, path, flags, attr):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
            return paramiko.SFTP_PERMISSION_DENIED
        handle = paramiko.SFTPHandle(flags)
        handle.readfile = self.local(path).open("rb")
        return handle


@pytest.fixture
def remote(tmp_path):
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports/orders.csv").write_text("id,customer\n1,MOCK A\n2,MOCK B\n")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    hostkey = paramiko.RSAKey.generate(2048)
    hosts = tmp_path / "known_hosts"
    hosts.write_text(f"[127.0.0.1]:{port} {hostkey.get_name()} {hostkey.get_base64()}\n")
    stop = Event()
    transports = []

    def serve():
        while not stop.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            transport = paramiko.Transport(client)
            transports.append(transport)
            transport.add_server_key(hostkey)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files, root=tmp_path)
            try:
                transport.start_server(server=Auth())
            except (paramiko.SSHException, OSError, EOFError):
                transport.close()

    thread = Thread(target=serve, daemon=True)
    thread.start()
    profile = {
        "kind": "sftp",
        "host": "127.0.0.1",
        "port": port,
        "username": "mock",
        "password": "fixture",
        "root": "/exports",
        "known_hosts": str(hosts),
    }
    try:
        yield profile
    finally:
        stop.set()
        thread.join(timeout=3)
        listener.close()
        for transport in transports:
            transport.close()


def test_real_sftp_import_host_pinning_and_root_boundaries(tmp_path, remote):
    def connector(path="orders.csv", profile=None):
        return SFTPConnector(
            ConnectorContext(
                tmp_path,
                "factory",
                {"kind": "sftp", "connection_ref": "reports", "path": path},
                {"factory": {"reports": profile or remote}},
            )
        )

    assert connector().discover()[0].name == "orders"
    first = connector().read("orders", "id", 1)
    assert first.rows == [{"id": "1", "customer": "MOCK A"}] and not first.exhausted
    assert connector().read("orders", "id", 1, checkpoint=first.checkpoint).exhausted
    for path in ("../private/secret.csv", "escape.csv"):
        with pytest.raises(DomainError, match="授权根目录"):
            connector(path).discover()
    unknown_hosts = tmp_path / "empty_hosts"
    unknown_hosts.write_text("")
    with pytest.raises(DomainError, match="指纹"):
        connector(profile={**remote, "known_hosts": str(unknown_hosts)}).discover()

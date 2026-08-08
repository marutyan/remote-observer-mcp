from remote_observer_mcp.config import HostConfig
from remote_observer_mcp.errors import ObserverError
from remote_observer_mcp.transports.base import Transport
from remote_observer_mcp.transports.local import LocalTransport
from remote_observer_mcp.transports.ssh import SshTransport


def transport_for_host(host: HostConfig) -> Transport:
    if host.transport == "local":
        return LocalTransport()
    if host.transport == "ssh" and host.ssh_alias is not None:
        return SshTransport(alias=host.ssh_alias)
    raise ObserverError("invalid_configuration", "host transport configuration is invalid")


__all__ = ["LocalTransport", "SshTransport", "Transport", "transport_for_host"]

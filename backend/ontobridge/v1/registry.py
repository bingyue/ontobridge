from .connector_sdk import ConnectorRegistry
from .db_connector import DatabaseConnector
from .file_connectors import FileConnector, SFTPConnector
from .rpa import WebRPAConnector


def create_registry():
    registry = ConnectorRegistry()
    registry.register(DatabaseConnector)
    registry.register(FileConnector)
    registry.register(SFTPConnector)
    registry.register(WebRPAConnector)
    registry.load_entry_points()
    return registry

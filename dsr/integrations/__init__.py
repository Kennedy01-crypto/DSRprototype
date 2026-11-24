"""Adapters between the DSR workflow and other systems.

- ``base``: the interfaces every connector and notifier implements.
- ``mock``: stand-ins used until real systems are connected.
- ``registry``: picks the implementation for each system from settings.

Jobs that call these adapters are created and run by ``dsr.services``.
"""

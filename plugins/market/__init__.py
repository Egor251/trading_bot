"""Market data / exchange connector packages.

These are discovered by ``ServiceRegistry`` (``*_service`` packages) from
``plugins/market/``, not from ``services/``. Config keys stay under
``services.<id>`` for compatibility with UI and existing YAML.

True infra services (runtime, universe, storage, bridge, …) remain in
``services/``.
"""

"""Direct cloud-game transport and device backend decoupling adapter."""

try:
    from .core import backend as _custom_backend
except Exception:
    _custom_backend = None

if _custom_backend is not None:
    backend = _custom_backend
else:
    try:
        # Verify optional dependencies for cloud protocol
        import aiortc  # noqa: F401
        _has_cloud_deps = True
    except Exception:
        _has_cloud_deps = False

    if _has_cloud_deps:
        class _CloudBackend:
            @staticmethod
            def create_device(config):
                from module.device.cloud.device import CloudDevice
                return CloudDevice(config)

            @property
            def CloudPanel(self):
                from module.webui.cloud import CloudPanel
                return CloudPanel

            @staticmethod
            def shutdown_all():
                from module.device.cloud.runtime import shutdown_all
                return shutdown_all()

            @staticmethod
            def add_preview_routes(app, key=None, cdn=None):
                from module.webui.cloud import add_preview_routes
                return add_preview_routes(app, key=key, cdn=cdn)

            @staticmethod
            def get_runtime(config_name):
                from module.device.cloud.runtime import get_runtime
                return get_runtime(config_name)

            @property
            def CloudConnectionError(self):
                from module.device.cloud.core.session import CloudConnectionError
                return CloudConnectionError

            @property
            def CloudProxy(self):
                from module.device.cloud.runtime import CloudProxy
                return CloudProxy

        backend = _CloudBackend()
    else:
        backend = None

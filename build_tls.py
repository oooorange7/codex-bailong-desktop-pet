"""Collect the TLS support data required by the selected Python runtime."""
import os
import ssl
from pathlib import Path


def tls_runtime_files():
    # Some bundled Python distributions add a certificate rejection resource
    # beside ssl.py. Keep that policy data beside the frozen ssl module too.
    # Never collect account certificates or paths supplied through environment.
    resource=Path(ssl.__file__).resolve().parent/'codex-rejected-cas.pem'
    return [(resource,'.')] if resource.is_file() else []


def pyinstaller_tls_args():
    # Fail before packaging if the build interpreter cannot initialize TLS.
    context=ssl.create_default_context()
    if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('构建运行库未启用标准 HTTPS 证书校验')
    return [arg for source,destination in tls_runtime_files()
            for arg in ('--add-data',str(source)+os.pathsep+destination)]

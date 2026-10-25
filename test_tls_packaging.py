"""A frozen-like ssl location must retain certificate and hostname checks."""
import os,shutil,ssl,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from build_tls import tls_runtime_files,pyinstaller_tls_args

class TlsPackagingTests(unittest.TestCase):
    def test_collected_resources_initialize_tls_beside_a_frozen_module(self):
        resources=tls_runtime_files()
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            for source,destination in resources:
                self.assertEqual(destination,'.');shutil.copy2(source,target/source.name)
            with patch.object(ssl,'__file__',str(target/'ssl.py')):
                context=ssl.create_default_context()
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
    def test_missing_runtime_resource_reproduces_the_frozen_failure(self):
        if not tls_runtime_files():self.skipTest('This Python runtime has no additional TLS data')
        with tempfile.TemporaryDirectory() as folder,patch.object(ssl,'__file__',str(Path(folder)/'ssl.py')):
            with self.assertRaises(FileNotFoundError):ssl.create_default_context()
    def test_only_runtime_support_data_is_collected(self):
        with patch.dict(os.environ,{'SSL_CERT_FILE':'C:/private-account/custom.pem'}):
            files=tls_runtime_files()
        self.assertTrue(all(path.name=='codex-rejected-cas.pem' for path,dest in files))
    def test_build_arguments_include_each_required_resource(self):
        args=pyinstaller_tls_args()
        for path,destination in tls_runtime_files():self.assertIn(str(path)+os.pathsep+destination,args)

if __name__=='__main__':unittest.main()

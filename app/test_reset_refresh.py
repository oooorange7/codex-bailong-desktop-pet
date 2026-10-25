"""Public feed failures stay truthful and recover without touching account data."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication,QLabel
from reset_watch import fetch_feed,reset_failure
from enhanced import ResetThread,EnhancedPet
from compact_bubble import Announcement

APP=QApplication.instance() or QApplication([])

class ResetRefreshTests(unittest.TestCase):
    def test_each_request_uses_current_proxy_configuration_and_sends_no_credentials(self):
        with patch('urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect=lambda *args,**kw:io.BytesIO(b'{"events":[],"scheduled":null}')
            fetch_feed();fetch_feed()
        self.assertEqual(factory.call_count,2)
        request=factory.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET');self.assertIsNone(request.data)
        self.assertFalse(request.has_header('Authorization'));self.assertFalse(request.has_header('Cookie'))

    def test_diagnostics_never_copy_private_paths_or_exception_messages(self):
        error=FileNotFoundError(2,'private text','C:/private-account/cert.pem')
        record=reset_failure(error)
        self.assertEqual(record['code'],'runtime_file')
        self.assertNotIn('private',json.dumps(record));self.assertNotIn('C:/',json.dumps(record))
        self.assertEqual(reset_failure(PermissionError('locked'),'cache')['code'],'cache_write')

    def worker(self,folder):
        with patch('enhanced.STATE_DIR',Path(folder)):worker=ResetThread()
        worker.tracker=Mock(snapshot={'checked':1});return worker

    def test_failed_request_keeps_cached_data_and_cannot_claim_a_fresh_result(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=self.worker(folder);ready=[];records=[];completed=[]
            worker.ready.connect(lambda *args:ready.append(args));worker.diagnostic.connect(records.append)
            def stop():worker.stop.set();worker.wake.set()
            def on_completed(*args):completed.append(args);stop()
            worker.completed.connect(on_completed)
            with patch('enhanced.fetch_feed',side_effect=FileNotFoundError(2,'missing')):worker.run()
            self.assertEqual(ready,[({'checked':1},[],False)])
            self.assertEqual(records[0]['code'],'runtime_file');self.assertEqual(completed,[(1,False)])
            worker.tracker.update.assert_not_called()

    def test_next_poll_recovers_after_a_failure_and_replaces_the_old_result(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=self.worker(folder);ready=[];completed=[]
            worker.tracker.update.return_value=({'checked':2,'pending':None},[])
            def on_ready(snapshot,alerts,fresh):
                ready.append((snapshot,fresh))
            def on_completed(run,success):
                completed.append((run,success))
                if success:worker.stop.set();worker.wake.set()
            worker.ready.connect(on_ready);worker.failed.connect(worker.wake.set)
            worker.completed.connect(on_completed)
            with patch('enhanced.fetch_feed',side_effect=[FileNotFoundError(2,'missing'),{}]):worker.run()
            self.assertEqual(ready,[({'checked':1},False),({'checked':2,'pending':None},True)])
            self.assertEqual(completed,[(1,False),(2,True)])

    def test_cache_failure_is_distinguished_from_network_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            worker=self.worker(folder);records=[];worker.diagnostic.connect(records.append)
            def stop():worker.stop.set();worker.wake.set()
            worker.failed.connect(stop);worker.tracker.update.side_effect=PermissionError('locked')
            with patch('enhanced.fetch_feed',return_value={}):worker.run()
            self.assertEqual((records[0]['code'],records[0]['phase']),('cache_write','cache'))

    def test_cached_card_marks_old_data_and_shows_last_successful_update(self):
        card=Announcement({'checked':1792735661,'pending':None},reset_failure(FileNotFoundError()),dense=True)
        card.setFixedWidth(155);card.show();APP.processEvents()
        text=' '.join(label.text() for label in card.findChildren(QLabel))
        self.assertIn('旧记录',text);self.assertIn('联网组件缺失',text);self.assertIn('上次 ',text)
        card.close();card.deleteLater();APP.processEvents()

    def test_fresh_response_clears_the_previous_error(self):
        pet=SimpleNamespace(bubble=SimpleNamespace(reset_feed_error=True,reset_feed_diagnostic={'code':'runtime_file'},redraw=Mock()),position_bubble=Mock())
        EnhancedPet.on_reset_feed(pet,{'checked':42,'pending':None},[],True)
        self.assertFalse(pet.bubble.reset_feed_error);self.assertIsNone(pet.bubble.reset_feed_diagnostic)
        self.assertEqual(pet.reset_checked,42)

if __name__=='__main__':unittest.main()

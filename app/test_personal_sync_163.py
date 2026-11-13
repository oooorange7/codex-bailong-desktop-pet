"""Regression cases adapted to the personal 0.8.x schema and UI contracts."""
import io,json,sqlite3,tempfile,time,unittest,urllib.error,ssl
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication,QLabel
from token_calendar import TokenUsageIndex
from token_analytics import usage_fingerprint
from enhanced_core import ActivityReducer,Session,RolloutTail
from reset_watch import ResetTracker,ResetCacheError,ResetFetchError,fetch_feed

APP=QApplication.instance() or QApplication([])
PARENT='11111111-1111-4111-8111-111111111111'
PAGE='22222222-2222-4222-8222-222222222222'
def event(typ,turn='one',**kw):
    return dict(type='event_msg',timestamp='2026-10-18T04:00:00Z',payload=dict(type=typ,turn_id=turn,**kw))
def sample(total,stamp,last=None):
    usage=dict(total_tokens=total,input_tokens=total-10,output_tokens=10)
    info=dict(total_token_usage=usage)
    if last is not None:info['last_token_usage']=dict(total_tokens=last,input_tokens=last,output_tokens=0)
    return dict(type='event_msg',timestamp=stamp,payload=dict(type='token_count',info=info))
def write(path,items,mode='w'):
    with path.open(mode,encoding='utf-8') as stream:
        for item in items:stream.write(json.dumps(item)+'\n')

class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        (self.home/'sessions').mkdir();self.cache=self.home/'usage.db'
        self.root=self.home/'sessions'/('rollout-'+PARENT+'.jsonl')
        self.page=self.home/'sessions'/('rollout-'+PARENT+'_'+PAGE+'.jsonl')
        self.header=dict(type='session_meta',payload=dict(id=PARENT,history_base=dict(thread_id=PARENT)))
        self.first=sample(1200,'2026-10-18T04:01:00Z',200)
        write(self.root,[dict(type='session_meta',payload=dict(id=PARENT)),sample(1000,'2026-10-18T04:00:00Z')])
        write(self.page,[self.header,self.first,sample(1500,'2026-10-18T04:02:00Z')])
    def tearDown(self):self.tmp.cleanup()
    def poll(self):return TokenUsageIndex(self.cache,self.home).poll(time.time())
    def test_continuation_counts_only_new_tokens_across_restarts_and_append(self):
        self.assertEqual(self.poll()['all_total'],1500)
        self.assertEqual(self.poll()['all_total'],1500)
        write(self.page,[sample(1700,'2026-10-18T04:03:00Z')],'a')
        self.assertEqual(self.poll()['all_total'],1700)
    def test_old_v2_overcount_is_corrected_once_without_losing_parent(self):
        self.poll()
        with closing(sqlite3.connect(self.cache)) as db,db:
            usage=self.first['payload']['info']['total_token_usage']
            fingerprint=usage_fingerprint(self.first,1200,usage)
            db.execute('UPDATE usage SET tokens=1200,input_tokens=1190,output_tokens=10 WHERE fingerprint=?',(fingerprint,))
            db.execute('DELETE FROM token_segment_repairs')
        self.assertEqual(self.poll()['all_total'],1500)
        self.assertEqual(self.poll()['all_total'],1500)
        with closing(sqlite3.connect(self.cache)) as db:
            self.assertEqual(db.execute('SELECT tokens FROM usage WHERE stamp=(SELECT MIN(stamp) FROM usage)').fetchone()[0],1000)
    def test_continuation_without_last_counter_never_invents_inherited_usage(self):
        write(self.page,[self.header,sample(1200,'2026-10-18T04:01:00Z'),sample(1500,'2026-10-18T04:02:00Z')])
        self.assertEqual(self.poll()['all_total'],1300)
    def test_repair_resumes_after_cancellation(self):
        self.poll()
        index=TokenUsageIndex(self.cache,self.home)
        with closing(index.connect()) as db,db:
            db.execute('DELETE FROM token_segment_repairs')
            from token_analytics import repair_segment
            repair_segment(db,self.page,str(self.page.resolve()),self.page.stat().st_size,lambda:True)
            self.assertEqual(db.execute('SELECT offset FROM token_segment_repairs WHERE path=?',(str(self.page.resolve()),)).fetchone()[0],0)
        self.assertEqual(self.poll()['all_total'],1500)
    def test_restart_with_progress_only_tail_does_not_mark_known_log_partial(self):
        self.poll();write(self.page,[event('thread_settings_applied')],'a')
        self.assertFalse(self.poll()['partial'])
    def test_previously_incomplete_header_is_retried(self):
        index=TokenUsageIndex(self.cache,self.home);key=str(self.page.resolve());index.format_known[key]=False
        self.assertFalse(index.poll()['partial'])
    def test_continuation_shares_activity_identity_and_child_stays_hidden(self):
        write(self.page,[self.header,event('task_started'),event('task_complete')])
        tail=RolloutTail(self.home);tail.poll()
        self.assertNotIn(self.page.stem,tail.reducer.sessions)
        self.assertEqual(len(tail.reducer.sessions),1)

class LifecycleTests(unittest.TestCase):
    def setUp(self):self.reducer=ActivityReducer()
    def test_late_command_cannot_reopen_completed_turn(self):
        for typ in ('task_started','task_complete'):self.reducer.apply('s',event(typ))
        self.assertEqual(self.reducer.apply('s',event('item_completed',item=dict(type='CommandExecution'))),[])
        self.assertTrue(self.reducer.sessions['s'].done)
        self.reducer.apply('s',event('task_started','two'))
        self.assertFalse(self.reducer.sessions['s'].done)
    def test_old_terminal_cannot_stop_new_turn(self):
        self.reducer.apply('s',event('task_started','two'))
        for typ in ('task_complete','turn_aborted'):
            self.assertEqual(self.reducer.apply('s',event(typ,'one')),[])
        self.assertEqual(self.reducer.terminate('s','completed','one'),[])
        self.assertFalse(self.reducer.sessions['s'].done)
    def test_idle_is_reversible_and_does_not_discard_completion(self):
        self.reducer.apply('s',event('task_started'))
        self.assertEqual(self.reducer.set_live_activity('s',False,'one')[0]['state'],'idle')
        self.assertFalse(self.reducer.sessions['s'].done)
        self.assertEqual(self.reducer.set_live_activity('s',True,'one')[0]['active'],1)
        self.assertEqual(sum(e['kind']=='complete' for e in self.reducer.terminate('s','completed','one')),1)
        self.assertEqual(self.reducer.terminate('s','completed','one'),[])
    def test_history_repair_does_not_notify_again(self):
        self.reducer.apply('s',event('item_completed',item=dict(type='CommandExecution')),historical=True)
        self.assertEqual(self.reducer.sessions['s'].turn,'one')
        self.assertFalse(any(e['kind']=='complete' for e in self.reducer.terminate('s','completed','one')))
    def test_ipc_terminal_has_exact_identity_and_no_private_text(self):
        from codex_live_status import LifecycleProjection
        output=[];projection=LifecycleProjection(Mock(),emit_activity=Mock(),emit_terminal=lambda *a:output.append(a))
        projection.states['s']=dict(turns=[dict(turnId='one',status='completed',text='PRIVATE')])
        projection.heartbeat(20);projection.heartbeat(40)
        self.assertEqual(output,[('s','completed','one')])
    def test_history_lookup_matches_exact_turn_not_newer_task(self):
        from session_read_state import terminal_conversations
        with tempfile.TemporaryDirectory() as tmp:
            with closing(sqlite3.connect(Path(tmp)/'thread_history_1.sqlite')) as db,db:
                db.execute('CREATE TABLE thread_turns(thread_id,turn_id,status,rollout_ordinal)')
                db.executemany('INSERT INTO thread_turns VALUES(?,?,?,?)',[('s','one','completed',1),('s','two','inProgress',2)])
            self.assertEqual(terminal_conversations(tmp,[('s','one'),('s','two')]),[('s','one','completed')])

class PersistenceTests(unittest.TestCase):
    def test_corrupt_chat_is_preserved_and_retry_can_recover(self):
        from chat_window import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp,'builtin');original=b'{broken'
            store.path.write_bytes(original);self.assertFalse(store.load())
            with self.assertRaises(OSError):store.new()
            self.assertEqual(store.path.read_bytes(),original)
            store.path.write_text('[]');self.assertTrue(store.load());store.new()
            self.assertEqual(len(json.loads(store.path.read_text(encoding='utf-8'))),1)
    def test_chat_changed_elsewhere_is_not_overwritten(self):
        from chat_window import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp,'builtin');store.new();store.path.write_text('[]')
            with self.assertRaises(OSError):store.save()
            self.assertEqual(store.path.read_text(),'[]')
    def test_unreadable_chat_and_credentials_never_write_defaults(self):
        from chat_window import SessionStore
        from desktop_services import SecretStore
        with patch('pathlib.Path.read_text',side_effect=PermissionError()),patch('chat_window.atomic_json') as write:
            store=SessionStore('unused','builtin')
            with self.assertRaises(OSError):store.save()
            write.assert_not_called()
        with patch('pathlib.Path.read_text',side_effect=PermissionError()),patch('desktop_services.atomic_json') as write:
            with self.assertRaises(OSError):SecretStore('unused').put('key','value')
            write.assert_not_called()
    def test_unreadable_settings_are_protected(self):
        from enhanced import EnhancedPet
        from desktop import DesktopPet
        with patch('pathlib.Path.read_text',side_effect=PermissionError()):
            self.assertEqual(EnhancedPet.read_settings(Path('unused')),({},True))
        fake=SimpleNamespace(settings_read_failed=True)
        with self.assertRaises(OSError):DesktopPet.apply_settings(fake,dict(sound=False))
        self.assertIs(EnhancedPet.save_settings(fake),False)
    def test_settings_saved_once_and_failure_cannot_commit_early(self):
        from desktop import DesktopPet
        fake=SimpleNamespace(enhanced_ready=True,settings_read_failed=False,collect_settings=Mock(),settings={},walk_direction=1,
            frameGeometry=lambda:QRect(0,0,256,256),x=lambda:0,y=lambda:0,width=lambda:256,height=lambda:256)
        with patch('desktop.atomic_json',side_effect=PermissionError()) as write:
            with self.assertRaises(PermissionError):DesktopPet.save_settings(fake)
            self.assertEqual(write.call_count,1)
    def test_atomic_failure_preserves_original_and_removes_temp(self):
        from desktop_core import atomic_json
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'settings.json';path.write_text('original')
            with patch('desktop_core.os.replace',side_effect=PermissionError()):
                with self.assertRaises(PermissionError):atomic_json(path,{'new':True})
            self.assertEqual(path.read_text(),'original');self.assertEqual(list(Path(tmp).iterdir()),[path])
    def test_reset_unreadable_cache_recovers_without_replacing_original(self):
        old=json.dumps(dict(initialized=True,seen=['old'],schedules=[],resolved=[]))
        with patch('pathlib.Path.read_text',side_effect=[PermissionError(),PermissionError(),old]),patch('desktop_core.atomic_json') as write:
            tracker=ResetTracker('unused')
            with self.assertRaises(ResetCacheError):tracker.update(dict(events=[],scheduled=None))
            write.assert_not_called();tracker.update(dict(events=[],scheduled=None))
            self.assertEqual(write.call_args.args[1]['seen'],['old'])
    def test_reset_fresh_result_survives_cache_write_failure(self):
        from enhanced import ResetThread
        with tempfile.TemporaryDirectory() as tmp,patch('enhanced.STATE_DIR',Path(tmp)):
            worker=ResetThread();results=[];diagnostics=[]
            def ready(snapshot,alerts,fresh):results.append(snapshot);worker.stop.set();worker.wake.set()
            worker.ready.connect(ready);worker.diagnostic.connect(diagnostics.append)
            with patch('enhanced.fetch_feed',return_value=dict(events=[],scheduled=None)),patch('desktop_core.atomic_json',side_effect=PermissionError()):worker.run()
            self.assertTrue(results[0]['checked']);self.assertEqual(diagnostics[-1]['code'],'cache_write')
    def test_cache_warning_card_does_not_claim_fresh_feed_is_stale(self):
        from compact_bubble import Announcement
        card=Announcement(dict(checked=time.time(),pending=None),dict(code='cache_write',message='缓存保存失败'),dense=True)
        text=' '.join(x.text() for x in card.findChildren(QLabel));card.close()
        self.assertIn('本次',text);self.assertNotIn('旧记录',text)
    def test_reset_retries_transient_but_never_bypasses_certificate(self):
        opener=Mock(side_effect=[urllib.error.URLError(TimeoutError()),io.BytesIO(b'{"events":[],"scheduled":null}')])
        with patch('reset_watch.time.sleep'):self.assertEqual(fetch_feed(opener)['events'],[])
        self.assertEqual(opener.call_count,2)
        opener=Mock(side_effect=urllib.error.URLError(ssl.SSLCertVerificationError(1,'verify failed')))
        with self.assertRaises(ResetFetchError) as result:fetch_feed(opener)
        self.assertEqual(result.exception.code,'certificate');self.assertEqual(opener.call_count,1)

if __name__=='__main__':unittest.main()

"""User-visible counting, selection, dismissal, live-idle and silent operation."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import time
import unittest
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QLineEdit,QVBoxLayout
from compact_bubble import CompactBubble
from enhanced_core import ActivityReducer,Session
from enhanced import EnhancedPet
from quota_summary_data import today_tokens
from token_charts import TokenChartCard
from codex_live_status import runtime_activity

APP=QApplication.instance() or QApplication([])


def finish(b):
    APP.processEvents()
    for _ in range(3):
        if b.size_transition.running:b.size_transition.complete()
        APP.processEvents()


class RequestedUiTests(unittest.TestCase):
    def setUp(self):
        self.b=CompactBubble();self.b.show();finish(self.b)
    def tearDown(self):
        self.b.close();self.b.deleteLater();APP.processEvents()

    def test_today_is_exact_not_rounded_and_groups_each_three_digits(self):
        now=datetime(2026,10,24,16).timestamp()
        row=today_tokens(dict(month='2026-10',days={'2026-10-24':87_990_123}),now)
        self.assertEqual(row['display'],'87,990,123')

    def test_bar_click_shows_that_day_and_click_again_restores_total(self):
        now=datetime(2026,10,24,16).timestamp()
        chart=TokenChartCard(dict(history_days={'2026-10-23':12_345_678,'2026-10-24':87_990_123}),period='7d',now=now)
        chart.resize(155,78);chart.show();APP.processEvents();chart.grab()
        plot=chart.plot;original=plot.header_text();self.assertTrue(original.startswith('7天'))
        rect,day=next((r,b) for r,b in plot.hits if b['start']=='2026-10-23')
        QTest.mouseClick(plot,Qt.MouseButton.LeftButton,pos=rect.center().toPoint())
        self.assertEqual(plot.header_text(),'10/23 · 12.35M')
        self.assertEqual(plot.series['total'],100_335_801)
        QTest.mouseClick(plot,Qt.MouseButton.LeftButton,pos=rect.center().toPoint())
        self.assertEqual(plot.header_text(),original);chart.close()

    def test_selected_day_survives_refresh_but_range_click_restores_total(self):
        day=datetime.now().strftime('%Y-%m-%d')
        self.b.token_snapshot=dict(history_days={day:87_990_123})
        self.b.select_token_range('7d');self.b.redraw();finish(self.b)
        plot=self.b.deck.content.plot;plot.grab();rect,_=plot.hits[-1]
        QTest.mouseClick(plot,Qt.MouseButton.LeftButton,pos=rect.center().toPoint())
        self.assertEqual(self.b.token_selection,(day,day))
        self.b.token_snapshot=dict(history_days={day:87_990_124});self.b.redraw();finish(self.b)
        self.assertIn('87.99M',self.b.deck.content.plot.header_text())
        self.assertEqual(self.b.deck.content.plot.selected_bin()['value'],87_990_124)
        self.b.select_token_range('7d')
        self.assertIsNone(self.b.token_selection);self.assertTrue(self.b.deck.content.plot.header_text().startswith('7天'))

    def test_outside_click_compacts_without_changing_saved_mode_or_other_rules(self):
        b=self.b;b.set_mode('auto');finish(b);b.show_status(True);finish(b)
        modes=[];b.modeChanged.connect(modes.append)
        self.assertFalse(b.outside_click.click_at(b.frameGeometry().center()))
        self.assertTrue(b.outside_click.click_at(b.frameGeometry().bottomRight()+QPoint(50,50)))
        finish(b);self.assertTrue(b.is_island());self.assertEqual(b.bubble_mode,'auto');self.assertFalse(modes)
        b.evaluate_mode(b.last_task_update+1);finish(b);self.assertTrue(b.is_island())
        b.task_updated(dict(kind='status',state='thinking',active=1));finish(b)
        self.assertTrue(b.is_island())
        QTest.mouseClick(b.island,Qt.MouseButton.LeftButton,pos=QPoint(80,20));finish(b)
        self.assertFalse(b.is_island());self.assertFalse(b._outside_compact)
        b.evaluate_mode(b.last_task_update+100);finish(b);self.assertTrue(b.is_island())
        b.task_updated(dict(kind='status',state='working',active=1));finish(b)
        self.assertFalse(b.is_island())

    def test_manual_collapse_survives_task_completion_and_presence_refresh(self):
        b=self.b;b.set_mode('auto');finish(b);b.collapse_from_outside();finish(b)
        for event in (dict(kind='status',session='a',state='working',active=1),
                      dict(kind='status',session='a',state='responding',active=1),
                      dict(kind='complete',session='a',turn='one',state='complete',active=0)):
            if event['kind']=='complete':b.status_indicator.notify_complete(event)
            else:b.status_indicator.observe(event)
            b.task_updated(event);finish(b)
            self.assertTrue(b.is_island())
        self.assertTrue(b.status_indicator.unread)
        for present in (False,True,None):
            b.codex_open=present;b.evaluate_mode(b.last_task_update+500);finish(b)
            self.assertTrue(b.is_island())
        self.assertTrue(b._outside_compact)

    def test_outside_click_queued_during_transition_stays_collapsed_on_next_event(self):
        b=self.b;b.set_mode('auto');finish(b);b.show_status(True)
        self.assertTrue(b.size_transition.running)
        b.collapse_from_outside();finish(b)
        b.task_updated(dict(kind='status',state='working',active=1));finish(b)
        self.assertTrue(b.is_island());self.assertTrue(b._outside_compact)

    def test_outside_click_hides_editor_without_discarding_draft(self):
        editor=QWidget();layout=QVBoxLayout(editor);field=QLineEdit('未保存的文字');layout.addWidget(field)
        self.b.show_page('chat',editor);finish(self.b)
        self.b.outside_click.click_at(self.b.frameGeometry().bottomRight()+QPoint(50,50));finish(self.b)
        self.assertTrue(self.b.is_island());self.assertEqual(field.text(),'未保存的文字')


class RuntimeReconciliationTests(unittest.TestCase):
    def test_late_commentary_after_log_completion_cannot_leave_a_busy_label(self):
        reducer=ActivityReducer();reducer.sessions['a']=Session(state='working',turn='one',done=True)
        event=reducer.set_live_activity('a',False,'one')[0]
        self.assertEqual((event['state'],event['active']),('idle',0))
    def test_only_known_runtime_idle_or_terminal_turn_proves_stop(self):
        turn={'turnId':'one','status':'inProgress'}
        self.assertIsNone(runtime_activity(dict(turns=[turn])))
        self.assertIsNone(runtime_activity(dict(turns=[turn],threadRuntimeStatus={'type':'unknown'})))
        self.assertIs(runtime_activity(dict(turns=[turn],threadRuntimeStatus={'type':'active'})),True)
        self.assertIs(runtime_activity(dict(turns=[turn],threadRuntimeStatus={'type':'idle'})),False)
        self.assertIs(runtime_activity(dict(turns=[dict(turn,status='completed')])),False)
    def test_explicit_idle_clears_stale_execution_but_preserves_another_active_task(self):
        reducer=ActivityReducer();reducer.sessions={'a':Session(state='working',turn='one',done=False),'b':Session(state='thinking',turn='two',done=False)}
        events=reducer.set_live_activity('a',False,'one')
        self.assertEqual((events[0]['state'],events[0]['active']),('idle',1))
        self.assertTrue(events[0]['runtime_reconciled']);self.assertFalse(any(e['kind']=='complete' for e in events))
        last=reducer.set_live_activity('b',False,'two')[0]
        b=CompactBubble();b.status_indicator.observe(dict(state='working',active=2))
        b.status_indicator.observe(last);self.assertFalse(b.status_indicator.is_busy());b.close()

    def test_silent_active_turn_and_stale_terminal_message_cannot_end_new_task(self):
        reducer=ActivityReducer();reducer.sessions['a']=Session(state='working',turn='new',done=False)
        self.assertFalse(reducer.set_live_activity('a',True,'new'))
        self.assertFalse(reducer.set_live_activity('a',False,'old'))
        self.assertFalse(reducer.sessions['a'].done)
        reducer.sessions['a'].done=True
        self.assertFalse(reducer.set_live_activity('a',True,'new'));self.assertTrue(reducer.sessions['a'].done)

    def test_disabled_popups_never_construct_a_windows_notifier(self):
        pet=SimpleNamespace(settings={'desktop_notifications':False},notify_history=[])
        with patch('silent_notify.SilentNotifier') as notifier:
            for title in ('Codex 任务已完成','额度提醒','重置公告'):EnhancedPet.notify(pet,title,'测试通知')
        notifier.assert_not_called();self.assertEqual(len(pet.notify_history),3)


if __name__=='__main__':unittest.main()

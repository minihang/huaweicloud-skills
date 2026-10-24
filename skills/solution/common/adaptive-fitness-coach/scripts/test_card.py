"""Run: python test_card.py. Uses temporary synthetic journals only; never touches
a real user journal. Logic tests run in-process (fast); two CLI contract tests
spawn subprocesses with an explicit timeout so a wedged environment fails loudly
instead of hanging (same protection as test_coach.py)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import card  # noqa: E402

SCRIPT = Path(__file__).with_name('card.py')
TIMEOUT = int(__import__('os').environ.get('AFC_TEST_TIMEOUT', '60'))


def make_db(exercise, sessions_spec):
    """sessions_spec: [(session_id, date, [ (reps, rpe|None), ... ])]"""
    ex = dict(exercise)
    day = {'name': '背', 'exercises': [ex]}
    db = {'schema_version': 1, 'profile': {}, 'plans': [],
          'sessions': []}
    db['plans'].append({'version': 1, 'created_at': '2026-01-01T00:00:00+00:00',
                        'plan': {'days': [day]}})
    for sid, date, sets in sessions_spec:
        logs = [dict(event_id='%s-r%d' % (sid, i), exercise_id=ex['id'], kind='work',
                     load_kg=ex.get('load_kg'), load_basis=ex['load_basis'],
                     reps=reps, rpe=rpe, quality='unknown', safety='none', feeling='x')
                for i, (reps, rpe) in enumerate(sets)]
        db['sessions'].append({'id': sid, 'date': date, 'status': 'completed',
                               'plan_version': 1, 'original_plan': day, 'plan': day,
                               'changes': [], 'logs': logs, 'summary': ''})
    return db


class CardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'journal.json'
        self.ex = {'id': 'pull-a', 'name': '高位下拉', 'sets': 3, 'reps_min': 8,
                   'reps_max': 12, 'load_kg': 40, 'load_basis': 'machine',
                   'rest_seconds': 120, 'target_rpe': 7}

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect_series_basic(self):
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7), (8, 7)]),
                               ('s2', '2026-10-08', [(10, 7), (10, None)]),
                               ('s3', '2026-10-15', [(12, 7), (12, 7)])])
        eid, meta, points = card.collect_series(db)
        self.assertEqual(eid, 'pull-a')
        self.assertEqual(meta['name'], '高位下拉')
        self.assertEqual([p['mean_reps'] for p in points], [8.0, 10.0, 12.0])
        self.assertEqual(points[1]['rpe_known'], 1)  # 未知 RPE 不虚构为已知
        self.assertEqual(points[0]['load_kg'], 40)

    def test_collect_series_excludes_warmup_and_superseded(self):
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7)]),
                               ('s2', '2026-10-08', [(10, 7)])])
        # 热身行不计入聚合（kind=warmup）
        db['sessions'][0]['logs'].append(
            dict(event_id='s1-warm', exercise_id=self.ex['id'], kind='warmup',
                 load_kg=20, load_basis='machine', reps=15, rpe=4,
                 quality='unknown', safety='none', feeling='爬坡'))
        # 被更正的行取链尾
        s2 = db['sessions'][1]
        s2['logs'][0]['superseded_by'] = 's2-r0-rev1'
        s2['logs'].append(dict(s2['logs'][0], event_id='s2-r0-rev1', superseded_by=None,
                               revises='s2-r0', reps=9))
        eid, _, points = card.collect_series(db)
        self.assertEqual([p['mean_reps'] for p in points], [8.0, 9.0])  # 热身与被更正行均不计
        self.assertEqual([p['work_sets'] for p in points], [1, 1])

    def test_collect_series_insufficient(self):
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7)])])
        with self.assertRaises(ValueError):
            card.collect_series(db)

    def test_collect_series_empty_db(self):
        with self.assertRaises(ValueError):
            card.collect_series({'schema_version': 1, 'profile': {}, 'plans': [], 'sessions': []})

    def test_collect_series_auto_selects_most_frequent(self):
        other = {'id': 'row-b', 'name': '坐姿划船', 'sets': 3, 'reps_min': 8,
                 'reps_max': 12, 'load_kg': None, 'load_basis': 'machine',
                 'rest_seconds': 120, 'target_rpe': 7}
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7)]), ('s2', '2026-10-08', [(9, 7)])])
        day2 = {'name': '背', 'exercises': [other]}
        for sid, date, reps in [('s1', '2026-10-01', 8), ('s2', '2026-10-08', 9),
                                ('s3', '2026-10-15', 10)]:
            db['sessions'].append({'id': sid + 'b', 'date': date, 'status': 'completed',
                                   'plan_version': 1, 'original_plan': day2, 'plan': day2,
                                   'changes': [], 'summary': '',
                                   'logs': [dict(event_id=sid + 'b-r0', exercise_id='row-b',
                                                 kind='work', load_kg=None,
                                                 load_basis='machine', reps=reps, rpe=7,
                                                 quality='unknown', safety='none', feeling='x')]})
        eid, _, points = card.collect_series(db)  # 不传 id：row-b 出现 3 次 > pull-a 2 次
        self.assertEqual(eid, 'row-b')
        self.assertEqual(len(points), 3)
        explicit_id, _, pts = card.collect_series(db, 'pull-a')
        self.assertEqual(explicit_id, 'pull-a')
        self.assertEqual(len(pts), 2)

    def test_auto_select_prefers_logged_over_planned(self):
        # Suggestion 回归：plan 出现 3 次但只记过 1 次的动作不得抢走记录完整的动作
        sparse = {'id': 'sparse-c', 'name': '罕练动作', 'sets': 3, 'reps_min': 8,
                  'reps_max': 12, 'load_kg': None, 'load_basis': 'machine',
                  'rest_seconds': 120, 'target_rpe': 7}
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7)]), ('s2', '2026-10-08', [(9, 7)])])
        # 三个 session 的 plan 都是 [pull-a, sparse-c]；sparse 只有 s1 记过一组
        day = {'name': '背', 'exercises': [self.ex, sparse]}
        for s in db['sessions']:
            s['plan'] = day
            s['original_plan'] = day
        db['sessions'][0]['logs'].append(
            dict(event_id='s1-c0', exercise_id='sparse-c', kind='work', load_kg=None,
                 load_basis='machine', reps=10, rpe=7, quality='unknown',
                 safety='none', feeling='x'))
        db['sessions'].append({'id': 's3', 'date': '2026-10-15', 'status': 'completed',
                               'plan_version': 1, 'original_plan': day, 'plan': day,
                               'changes': [], 'summary': '', 'logs': []})
        eid, _, points = card.collect_series(db)  # pull-a 有 2 个点，sparse 只有 1 个
        self.assertEqual(eid, 'pull-a')
        self.assertEqual(len(points), 2)

    def test_render_svg_unparseable_date_not_silent(self):
        # P2 回归：垃圾日期必须显式提示，不得静默当作正常间隔
        points = [{'date': 'not-a-date', 'session_id': 's1', 'work_sets': 2,
                   'mean_reps': 8.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 40},
                  {'date': '2026-10-08', 'session_id': 's2', 'work_sets': 2,
                   'mean_reps': 9.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 40}]
        svg = card.render_svg(points, {'name': '下拉', 'reps_min': 8, 'reps_max': 12})
        self.assertIn('无法解析', svg)  # 不宣称进步，也不静默
        self.assertNotIn('逐次进步', svg)

    def test_render_svg_load_varied_note(self):
        # Suggestion 回归：次数递增但负荷下降时，不得只说"逐次进步"
        points = [{'date': '2026-10-01', 'session_id': 's1', 'work_sets': 2,
                   'mean_reps': 8.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 40},
                  {'date': '2026-10-08', 'session_id': 's2', 'work_sets': 2,
                   'mean_reps': 10.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 35},
                  {'date': '2026-10-15', 'session_id': 's3', 'work_sets': 2,
                   'mean_reps': 12.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 30}]
        svg = card.render_svg(points, {'name': '下拉', 'reps_min': 8, 'reps_max': 12})
        self.assertIn('负荷有变化', svg)

    def test_render_svg_contains_series(self):
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7), (8, 7)]),
                               ('s2', '2026-10-08', [(10, 7), (10, 7)]),
                               ('s3', '2026-10-15', [(12, 7), (12, 7)])])
        _, meta, points = card.collect_series(db)
        svg = card.render_svg(points, meta)
        self.assertIn('高位下拉', svg)
        self.assertIn('8', svg)
        self.assertIn('10', svg)
        self.assertIn('12', svg)
        self.assertIn('10/01', svg)  # X 轴日期
        self.assertIn('次数区间内逐次进步', svg)
        self.assertIn('</svg>', svg)

    def test_render_svg_escapes_xml(self):
        meta = {'name': '下拉<背&腿>"引体"', 'reps_min': 8, 'reps_max': 12}
        points = [{'date': '2026-10-01', 'session_id': 's1', 'work_sets': 1,
                   'mean_reps': 8.0, 'rpe_known': 1, 'rpe_total': 1, 'load_kg': 40},
                  {'date': '2026-10-08', 'session_id': 's2', 'work_sets': 1,
                   'mean_reps': 9.0, 'rpe_known': 1, 'rpe_total': 1, 'load_kg': 40}]
        svg = card.render_svg(points, meta)
        self.assertNotIn('<背', svg)
        self.assertNotIn('&腿', svg)
        self.assertIn('&amp;', svg)
        self.assertIn('&lt;', svg)

    def test_render_svg_long_gap_note(self):
        points = [{'date': '2026-01-01', 'session_id': 's1', 'work_sets': 2,
                   'mean_reps': 8.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 40},
                  {'date': '2026-10-01', 'session_id': 's2', 'work_sets': 2,
                   'mean_reps': 9.0, 'rpe_known': 2, 'rpe_total': 2, 'load_kg': 40}]
        svg = card.render_svg(points, {'name': '下拉', 'reps_min': 8, 'reps_max': 12})
        self.assertIn('中断', svg)  # 273 天间隔必须提示，不许静默
        self.assertIn('跨段记录', svg)  # 跨段不得宣称"逐次进步"（与 review 的 long_gap 语义一致）

    def test_render_svg_no_rpe_no_fake_average(self):
        points = [{'date': '2026-10-01', 'session_id': 's1', 'work_sets': 2,
                   'mean_reps': 8.0, 'rpe_known': 0, 'rpe_total': 2, 'load_kg': None},
                  {'date': '2026-10-08', 'session_id': 's2', 'work_sets': 2,
                   'mean_reps': 9.0, 'rpe_known': 0, 'rpe_total': 2, 'load_kg': None}]
        svg = card.render_svg(points, {'name': '档位机器', 'reps_min': 8, 'reps_max': 12})
        self.assertIn('0/4', svg)          # 如实展示记录率
        self.assertNotIn('平均 RPE', svg)  # 未知 RPE 不虚构平均值
        self.assertNotIn('负荷', svg)      # 档位机器无公斤负荷，不标注重量

    def test_cli_generates_file_and_summary(self):
        db = make_db(self.ex, [('s1', '2026-10-01', [(8, 7), (8, 7)]),
                               ('s2', '2026-10-08', [(10, 7), (10, 7)])])
        self.data.write_text(json.dumps(db, ensure_ascii=False), encoding='utf-8')
        out = Path(self.tmp.name) / 'card.svg'
        r = subprocess.run([sys.executable, str(SCRIPT), '--data', str(self.data),
                            '--output', str(out)], capture_output=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8'))
        summary = json.loads(r.stdout.decode('utf-8'))
        self.assertEqual(summary['sessions'], 2)
        self.assertEqual(summary['rpe_known'], 4)
        svg = out.read_text(encoding='utf-8')
        self.assertIn('高位下拉', svg)

    def test_cli_rejects_relative_and_missing_paths(self):
        r = subprocess.run([sys.executable, str(SCRIPT), '--data', 'nope.json',
                            '--output', 'x.svg'], capture_output=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 2)
        err = json.loads(r.stderr.decode('utf-8'))
        self.assertIn('绝对路径', err['error'])

    def test_cli_warns_on_unparseable_date(self):
        # P2 回归：CLI 摘要必须带 warnings，不得静默生成"看似正常"的卡片
        db = make_db(self.ex, [('s1', 'not-a-date', [(8, 7)]),
                               ('s2', '2026-10-08', [(9, 7)])])
        self.data.write_text(json.dumps(db, ensure_ascii=False), encoding='utf-8')
        out = Path(self.tmp.name) / 'card.svg'
        r = subprocess.run([sys.executable, str(SCRIPT), '--data', str(self.data),
                            '--output', str(out)], capture_output=True, timeout=TIMEOUT)
        self.assertEqual(r.returncode, 0, r.stderr.decode('utf-8'))
        summary = json.loads(r.stdout.decode('utf-8'))
        self.assertIn('warnings', summary)
        self.assertTrue(any('not-a-date' in w for w in summary['warnings']))


if __name__ == '__main__':
    unittest.main()

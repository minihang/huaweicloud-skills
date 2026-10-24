"""Run: python3 test_coach.py. Uses only temporary synthetic journals.

Every child process is spawned with an explicit timeout (default 60s per call,
override with AFC_TEST_TIMEOUT). A slow or wedged environment must fail loudly
with subprocess.TimeoutExpired instead of hanging the suite indefinitely."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('coach.py')
TIMEOUT = int(os.environ.get('AFC_TEST_TIMEOUT', '60'))

class CoachTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)/'journal.json'
        self.e={'id':'pull-machine-a','name':'下拉','sets':2,'reps_min':8,'reps_max':12,'load_kg':40,'load_basis':'machine','rest_seconds':120,'target_rpe':7}
        self.plan={'days':[{'name':'背','exercises':[self.e]}]}
    def tearDown(self): self.tmp.cleanup()
    def run_op(self, op, p=None, fail=False):
        # 按脚本的编码硬约定以 UTF-8 读写子进程（模拟宿主职责）；
        # 显式 timeout：挂起环境快速失败而非无限阻塞
        r=subprocess.run([sys.executable,str(SCRIPT),op,'--data',str(self.data)],
                         input=json.dumps(p or {}).encode('utf-8'),capture_output=True,
                         timeout=TIMEOUT)
        out=(r.stderr if fail else r.stdout).decode('utf-8')
        self.assertEqual(r.returncode,2 if fail else 0,out)
        return json.loads(out)
    def start(self,sid='s1',date='2026-10-23'):
        self.run_op('plan', self.plan)
        self.run_op('start',{'session_id':sid,'date':date})
    def row(self,event='r1',**extra):
        return dict(event_id=event,exercise_id=self.e['id'],kind='work',load_kg=40,load_basis='machine',reps=12,rpe=7,quality='good',safety='none',feeling='测试',**extra)
    def log(self,row,sid='s1',**kw): return self.run_op('log',{'session_id':sid,'set':row},**kw)
    def test_roundtrip_idempotency_and_conflict(self):
        self.start(); row=self.row(); self.log(row)
        self.assertTrue(self.log(row)['duplicate'])
        changed=copy.deepcopy(row); changed['reps']=10
        self.log(changed,fail=True)
        self.assertEqual(len(self.run_op('inspect')['sessions'][0]['logs']),1)
    def test_safety_unknown_rpe_and_units(self):
        self.start()
        for i,(safety,action) in enumerate([('pain','stop_exercise'),('urgent','stop_session')]):
            row=self.row(str(i)); row['safety']=safety
            self.assertEqual(self.log(row)['suggestion']['action'],action)
        row=self.row('unknown'); row['rpe']=None
        self.assertEqual(self.log(row)['suggestion']['action'],'observe')
        row=self.row('bad'); row['load_basis']='per_hand'; self.log(row,fail=True)
    def test_amend_and_original_preserved(self):
        self.start(); self.log(self.row()); self.log(self.row('r2'))
        day=copy.deepcopy(self.plan['days'][0]); day['exercises'][0]['sets']=1
        self.run_op('amend',{'session_id':'s1','reason':'时间','day':day},fail=True)
        day['exercises'][0]['sets']=3
        self.run_op('amend',{'session_id':'s1','reason':'用户增加组','day':day})
        s=self.run_op('inspect')['sessions'][0]
        self.assertEqual(s['original_plan']['exercises'][0]['sets'],2)
        self.assertEqual(s['plan']['exercises'][0]['sets'],3)
        stats=self.run_op('finish',{'session_id':'s1'})
        self.assertAlmostEqual(stats['completion_ratio'],2/3)
        self.assertEqual(stats['volume_kg_reps_by_exercise_and_basis']['pull-machine-a:machine'],960)
    def test_finish_dual_completion_ratio(self):
        self.start(); self.log(self.row()); self.log(self.row('r2'))
        day=copy.deepcopy(self.plan['days'][0]); day['exercises'][0]['sets']=3
        self.run_op('amend',{'session_id':'s1','reason':'增加组','day':day})
        stats=self.run_op('finish',{'session_id':'s1'})
        self.assertAlmostEqual(stats['completion_ratio'],2/3)
        self.assertAlmostEqual(stats['completion_ratio_original'],1.0)
        self.assertEqual(stats['planned_work_sets_original'],2)
        self.assertEqual(stats['planned_work_sets'],3)
    def test_longitudinal_three_sessions(self):
        for i in range(3):
            sid='s'+str(i); self.start(sid)
            for j in range(2): self.log(self.row('r'+str(j)),sid)
            self.run_op('finish',{'session_id':sid})
        r=self.run_op('review')
        self.assertEqual(r['exercise_reviews'][0]['decision'],'consider_smallest_load_increment')
        self.assertEqual(r['exercise_reviews'][0]['blocked_by'],[])
    def test_revise_chain_and_stats(self):
        self.start(); self.log(self.row('r1'))
        row2=self.row('r1-rev1'); row2['reps']=9
        out=self.run_op('revise',{'session_id':'s1','event_id':'r1','set':row2})
        self.assertEqual(out['effective'],'r1-rev1')
        self.assertEqual(out['stats']['work_sets'],1)
        self.assertEqual(out['stats']['volume_kg_reps_by_exercise_and_basis']['pull-machine-a:machine'],360)
        out2=self.run_op('revise',{'session_id':'s1','event_id':'r1','set':row2})
        self.assertTrue(out2['duplicate'])
        row3=self.row('r1-rev2'); row3['reps']=8
        self.run_op('revise',{'session_id':'s1','event_id':'r1','set':row3},fail=True)
        out3=self.run_op('revise',{'session_id':'s1','event_id':'r1-rev1','set':row3})
        self.assertEqual(out3['effective'],'r1-rev2')
        self.run_op('finish',{'session_id':'s1'})
        row4=self.row('r1-rev3'); row4['reps']=7
        out4=self.run_op('revise',{'session_id':'s1','event_id':'r1-rev2','set':row4})
        self.assertEqual(out4['effective'],'r1-rev3')
        self.log(self.row('r9'),fail=True)
    def test_review_hard_and_blocked(self):
        for i in range(3):
            sid='h'+str(i); self.start(sid)
            for j in range(2):
                row=self.row(sid+'-r'+str(j)); row['rpe']=10
                self.log(row,sid)
            self.run_op('finish',{'session_id':sid})
        r=self.run_op('review'); er=r['exercise_reviews'][0]
        self.assertEqual(er['decision'],'review_recovery_and_reduce')
        self.assertIn('rpe_above_target',er['blocked_by'])
        self.assertTrue(er['same_day_sessions'])
    def test_review_insufficient_data(self):
        for i in range(2):
            sid='i'+str(i); self.start(sid)
            for j in range(2): self.log(self.row(sid+'-r'+str(j)),sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertEqual(er['decision'],'insufficient_data')
        self.assertEqual(er['blocked_by'],['insufficient_data'])
    def test_review_blocked_by_unknown_rpe(self):
        for i in range(3):
            sid='u'+str(i); self.start(sid)
            for j in range(2):
                row=self.row(sid+'-r'+str(j)); row['rpe']=None; row['quality']='unknown'
                self.log(row,sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertEqual(er['decision'],'maintain_and_observe')
        self.assertIn('rpe_unknown',er['blocked_by'])
    def test_review_sorts_by_date(self):
        for sid,date in [('s1','2026-10-25'),('s2','2026-10-23'),('s3','2026-10-24')]:
            self.start(sid,date)
            for j in range(2): self.log(self.row(sid+'-r'+str(j)),sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertEqual([e['date'] for e in er['entries']],['2026-10-23','2026-10-24','2026-10-25'])
        self.assertEqual([e['session_id'] for e in er['entries']],['s2','s3','s1'])
        self.assertFalse(er['same_day_sessions'])
        self.assertEqual(er['entries'][0]['day'],'背')
    def test_purge_requires_confirm_and_cleans_bak(self):
        self.start(); self.log(self.row())
        self.run_op('purge',{},fail=True)
        self.assertTrue(self.data.exists())
        self.run_op('purge',{'confirm':'PURGE'})
        self.assertEqual(self.run_op('inspect')['sessions'],[])
        self.assertFalse(Path(str(self.data)+'.bak').exists())
    def test_purge_sessions_scope(self):
        for sid in ('s1','s2'):
            self.start(sid)
            self.log(self.row('r1'),sid)
            self.run_op('finish',{'session_id':sid})
        self.run_op('purge',{'scope':'sessions','session_ids':['s2'],'confirm':'PURGE'})
        db=self.run_op('inspect')
        self.assertEqual([s['id'] for s in db['sessions']],['s1'])
        self.run_op('purge',{'scope':'sessions','session_ids':['nope'],'confirm':'PURGE'},fail=True)
    def test_skill_dir_data_rejected(self):
        inside=SCRIPT.parent.parent/'journal.json'
        r=subprocess.run([sys.executable,str(SCRIPT),'inspect','--data',str(inside)],
                         capture_output=True,timeout=TIMEOUT)
        self.assertEqual(r.returncode,2)
        json.loads(r.stderr.decode('utf-8'))  # 输出必须是合法 UTF-8 JSON
    def test_start_bounds(self):
        self.run_op('plan',self.plan)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23','day_index':5},fail=True)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23','plan_version':9},fail=True)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23','plan_version':1})
        s=self.run_op('inspect')['sessions'][0]
        self.assertEqual(s['plan_version'],1)
    def test_profile_null_deletes_key(self):
        self.run_op('profile',{'goal':'hypertrophy','note':'临时'})
        prof=self.run_op('profile',{'note':None})
        self.assertNotIn('note',prof)
        self.assertEqual(prof['goal'],'hypertrophy')
    def test_corrupt_file_not_reset(self):
        self.data.write_text('{broken')
        self.run_op('profile',{'goal':'strength'},fail=True)
        self.assertEqual(self.data.read_text(),'{broken')
    def test_failed_validation_atomic(self):
        self.start(); before=self.data.read_bytes()
        row=self.row(); row['rpe']=11
        self.log(row,fail=True)
        self.assertEqual(self.data.read_bytes(),before)
        self.assertTrue(Path(str(self.data)+'.bak').exists())
    def test_reduction(self):
        self.start(); row=self.row(); row['rpe']=10
        r=self.log(row)['suggestion']
        self.assertEqual(r['load_kg'],38)
        self.assertEqual(r['rest_seconds'],150)
    def test_reduction_rest_cap_600(self):
        # P1 回归：rest 上限是 600（schema 合法上限），不是 300；
        # RPE 超标时建议休息不得短于计划值
        heavy=dict(self.e,rest_seconds=360)
        self.run_op('plan',{'days':[{'name':'背','exercises':[heavy]}]})
        self.run_op('start',{'session_id':'s1','date':'2026-10-23'})
        row=self.row('r1'); row['rpe']=10
        r=self.log(row)['suggestion']
        self.assertEqual(r['rest_seconds'],390)  # 360+30，不被 300 截断
    def test_suggestion_warmup_low_reps_ok(self):
        # Suggestion 回归：正式条目上的低次数爬坡热身组（reps<reps_min）不触发
        # reduce_warmup_load——低次数爬坡属正常设计，仅 RPE 超标才回调
        self.start()
        row=self.row('w1'); row['kind']='warmup'; row['reps']=5; row['rpe']=6
        self.assertEqual(self.log(row)['suggestion']['action'],'observe')
        row2=self.row('w2'); row2['kind']='warmup'; row2['reps']=5; row2['rpe']=9
        self.assertEqual(self.log(row2)['suggestion']['action'],'reduce_warmup_load')
    def test_review_warns_unparseable_date(self):
        # P2 回归：垃圾日期不得静默排序/漏检，review 输出 warnings
        import datetime as _dt
        db={'schema_version':1,'profile':{},'plans':[],'sessions':[]}
        plan_entry={'days':[{'name':'背','exercises':[self.e]}]}
        for sid,date in [('bad','not-a-date'),('good','2026-10-23')]:
            day=copy.deepcopy(plan_entry['days'][0])
            sess={'id':sid,'date':date,'status':'completed','plan_version':1,
                  'original_plan':day,'plan':day,'changes':[],'summary':''}
            sess['logs']=[dict(event_id=sid+'-r1',exercise_id=self.e['id'],kind='work',
                               load_kg=40,load_basis='machine',reps=12,rpe=7,
                               quality='good',safety='none',feeling='x')]
            db['sessions'].append(sess)
        db['plans'].append({'version':1,'created_at':_dt.datetime.now(_dt.timezone.utc).isoformat(),'plan':plan_entry})
        self.data.write_text(json.dumps(db,ensure_ascii=False),encoding='utf-8')
        r=self.run_op('review')
        self.assertIn('warnings',r)
        self.assertTrue(any('bad' in w for w in r['warnings']))
    def test_utf8_stdin_raw_chinese(self):
        # 直接以 UTF-8 字节传中文（模拟 PowerShell 管道），在任意 locale 下不得损坏
        self.start()
        payload=json.dumps({'session_id':'s1','set':self.row('r1')},ensure_ascii=False).encode('utf-8')
        r=subprocess.run([sys.executable,str(SCRIPT),'log','--data',str(self.data)],
                         input=payload,capture_output=True,timeout=TIMEOUT)
        self.assertEqual(r.returncode,0,r.stderr.decode('utf-8'))
        out=json.loads(r.stdout.decode('utf-8'))  # 输出必须是合法 UTF-8 JSON
        self.assertIn('suggestion',out)
        db=self.run_op('inspect')
        self.assertEqual(db['sessions'][0]['logs'][0]['feeling'],'测试')  # 持久化无损坏
    def test_review_machine_null_load(self):
        mach={'id':'row-machine-b','name':'坐姿划船','sets':2,'reps_min':8,'reps_max':12,'load_kg':None,'load_basis':'machine','rest_seconds':120,'target_rpe':7}
        plan={'days':[{'name':'背','exercises':[mach]}]}
        for i in range(3):
            sid='m'+str(i); self.run_op('plan',plan)
            self.run_op('start',{'session_id':sid,'date':'2026-10-2'+str(i)})
            for j in range(2):
                row=dict(event_id=sid+'-r'+str(j),exercise_id=mach['id'],kind='work',load_kg=None,load_basis='machine',reps=12,rpe=7,quality='good',safety='none',feeling='档位8')
                self.log(row,sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertEqual(er['decision'],'consider_smallest_load_increment')
        self.assertEqual(er['blocked_by'],[])
        self.assertTrue(er['load_not_tracked'])
    def test_start_bool_rejected(self):
        self.run_op('plan',self.plan)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23','plan_version':True},fail=True)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23','day_index':True},fail=True)
    def test_start_date_normalized(self):
        # ISO 基本格式（20261023）仅 Python 3.11+ 的 fromisoformat 接受；
        # 声明 3.9+ 的技能在旧解释器上须如实拒绝而非测试失败
        if sys.version_info >= (3, 11):
            self.run_op('plan',self.plan)
            self.run_op('start',{'session_id':'s1','date':'20261023'})
            s=self.run_op('inspect')['sessions'][0]
            self.assertEqual(s['date'],'2026-10-23')
        else:
            self.run_op('plan',self.plan)
            self.run_op('start',{'session_id':'s1','date':'20261023'},fail=True)
    def test_profile_target_rpe_null_deletes(self):
        self.run_op('profile',{'target_rpe':7})
        prof=self.run_op('profile',{'target_rpe':None})
        self.assertNotIn('target_rpe',prof)
        self.run_op('profile',{'target_rpe':11},fail=True)
    def test_log_idempotent_with_explicit_defaults(self):
        self.start()
        row1=self.row('r1')  # 省略 safety/quality 默认字段的等价形式
        row2=dict(row1,safety='none',quality='unknown')
        r1=self.run_op('log',{'session_id':'s1','set':{k:v for k,v in row1.items() if k not in ('safety','quality')}})
        r2=self.run_op('log',{'session_id':'s1','set':row2})
        self.assertTrue(r2['duplicate'])
    def test_revise_idempotent_with_explicit_defaults(self):
        # 首次 revise 省略默认字段，重试显式补默认值（语义等价）应返回 duplicate 而非报冲突
        self.start(); self.log(self.row('r1'))
        minimal=dict(event_id='r1-rev1',exercise_id=self.e['id'],load_kg=40,load_basis='machine',reps=9,rpe=7,feeling='更正')
        self.run_op('revise',{'session_id':'s1','event_id':'r1','set':minimal})
        explicit=dict(minimal,kind='work',quality='unknown',safety='none')
        out=self.run_op('revise',{'session_id':'s1','event_id':'r1','set':explicit})
        self.assertTrue(out['duplicate'])
        self.assertEqual(out['effective'],'r1-rev1')
    def test_review_legacy_basic_date_sorted(self):
        # 遗留日志含 ISO 基本格式日期时，review 读取侧仍按真实日期排序
        import datetime as _dt
        db={'schema_version':1,'profile':{},'plans':[],'sessions':[]}
        plan_entry={'days':[{'name':'背','exercises':[self.e]}]}
        for sid,date in [('old','20260101'),('new','2026-10-23')]:
            day=copy.deepcopy(plan_entry['days'][0])
            sess={'id':sid,'date':date,'status':'completed','plan_version':1,'original_plan':day,'plan':day,'changes':[],'summary':''}
            sess['logs']=[dict(event_id=sid+'-r1',exercise_id=self.e['id'],kind='work',load_kg=40,load_basis='machine',reps=12,rpe=7,quality='good',safety='none',feeling='x')]
            db['sessions'].append(sess)
        db['plans'].append({'version':1,'created_at':_dt.datetime.now(_dt.timezone.utc).isoformat(),'plan':plan_entry})
        self.data.write_text(json.dumps(db,ensure_ascii=False),encoding='utf-8')
        entries=self.run_op('review')['exercise_reviews'][0]['entries']
        self.assertEqual([e['session_id'] for e in entries],['old','new'])
    def test_warmup_entry_excluded_from_completion(self):
        # P1 回归：计划内热身条目（planned_kind=warmup）不得稀释完成率，也不进 review 聚合
        warm={'id':'warm-band','name':'激活','sets':1,'reps_min':10,'reps_max':15,'load_kg':None,'load_basis':'machine','rest_seconds':30,'target_rpe':4,'planned_kind':'warmup'}
        plan={'days':[{'name':'背','exercises':[warm,dict(self.e,sets=3)]}]}
        self.run_op('plan',plan)
        self.run_op('start',{'session_id':'s1','date':'2026-10-23'})
        self.run_op('log',{'session_id':'s1','set':{'event_id':'w1','exercise_id':'warm-band','kind':'warmup','load_kg':None,'load_basis':'machine','reps':12,'rpe':3,'feeling':'热身'}})
        for i in range(3):
            self.log(self.row('p'+str(i)))
        st=self.run_op('finish',{'session_id':'s1'})
        self.assertEqual(st['completion_ratio'],1.0)
        self.assertEqual(st['planned_work_sets'],3)
        r=self.run_op('review')['exercise_reviews']
        self.assertEqual([x['exercise_id'] for x in r],['pull-machine-a'])
    def test_work_row_on_warmup_entry_rejected(self):
        # 正式组记到热身条目上应被拒绝（防止热身条目变成完成率注水口）
        warm={'id':'warm-band','name':'激活','sets':1,'reps_min':10,'reps_max':15,'load_kg':None,'load_basis':'machine','rest_seconds':30,'target_rpe':4,'planned_kind':'warmup'}
        self.run_op('plan',{'days':[{'name':'背','exercises':[warm]}]})
        self.run_op('start',{'session_id':'s1','date':'2026-10-23'})
        self.log(self.row('r1'),fail=True)  # 默认 kind=work 打在 warmup 条目上
    def test_amend_cannot_demote_recorded_entry_to_warmup(self):
        # 已记录正式组的条目不能通过 amend 降级为热身（防完成率虚增）
        self.start()
        self.log(self.row('r1'))
        day=copy.deepcopy(self.plan['days'][0])
        day['exercises'][0]['planned_kind']='warmup'
        self.run_op('amend',{'session_id':'s1','reason':'作弊降级','day':day},fail=True)
    def test_planned_kind_invalid_value_rejected(self):
        plan={'days':[{'name':'背','exercises':[dict(self.e,planned_kind='rest')]}]}
        self.run_op('plan',plan,fail=True)
    def test_review_long_gap_blocked(self):
        # P2 回归：中断超过阈值的"三次可比"不得建议进阶
        for sid,date in [('g1','2025-01-01'),('g2','2025-01-08'),('g3','2026-10-20')]:
            self.start(sid,date)
            for j in range(2): self.log(self.row(sid+'-r'+str(j)),sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertIn('long_gap',er['blocked_by'])
        self.assertNotEqual(er['decision'],'consider_smallest_load_increment')
        self.assertIn('long_gap_days_used',self.run_op('review'))
    def test_review_long_gap_threshold_adjustable(self):
        # 用户可通过 profile.long_gap_days 调整阈值；null 删除恢复默认；非法值拒绝
        # 注意 setUp 未清 profile：先显式清掉再设值，避免串档
        self.run_op('profile',{'long_gap_days':None})
        self.run_op('profile',{'long_gap_days':365})  # 间隔 31/260 天：365 阈值下不算 long_gap，默认 21 下算
        for sid,date in [('g1','2026-01-01'),('g2','2026-02-01'),('g3','2026-10-20')]:
            self.start(sid,date)
            for j in range(2): self.log(self.row(sid+'-r'+str(j)),sid)
            self.run_op('finish',{'session_id':sid})
        r=self.run_op('review')
        self.assertEqual(r['long_gap_days_used'],365)
        self.assertNotIn('long_gap',r['exercise_reviews'][0]['blocked_by'])
        self.run_op('profile',{'long_gap_days':None})  # 删除恢复默认 21
        self.assertEqual(self.run_op('review')['long_gap_days_used'],21)
        self.run_op('profile',{'long_gap_days':0},fail=True)
        self.run_op('profile',{'long_gap_days':True},fail=True)
    def test_review_reps_progressing_in_range(self):
        # P3 回归：8→10→12 区间内逐次进步应输出 reps_progressing_in_range
        for sid,date,reps in [('p1','2026-10-01',8),('p2','2026-10-08',10),('p3','2026-10-15',12)]:
            self.start(sid,date)
            for j in range(2):
                row=self.row(sid+'-r'+str(j)); row['reps']=reps
                self.log(row,sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertEqual(er['decision'],'reps_progressing_in_range')
        self.assertEqual([e['mean_reps'] for e in er['entries']],[8.0,10.0,12.0])
        self.assertEqual([e['gap_days'] for e in er['entries']],[None,7,7])
    def test_review_progressing_suppressed_by_gap(self):
        # 进步曲线遇上 long_gap：进步结论被压制（中断后的进步不可比）
        self.run_op('profile',{'long_gap_days':21})
        for sid,date,reps in [('p1','2026-01-01',8),('p2','2026-01-08',10),('p3','2026-10-15',12)]:
            self.start(sid,date)
            for j in range(2):
                row=self.row(sid+'-r'+str(j)); row['reps']=reps
                self.log(row,sid)
            self.run_op('finish',{'session_id':sid})
        er=self.run_op('review')['exercise_reviews'][0]
        self.assertNotEqual(er['decision'],'reps_progressing_in_range')
        self.assertIn('long_gap',er['blocked_by'])
    def test_suggestion_zero_load_reduction(self):
        # P4 回归：load_kg=0（空杆）是合法值，不得被 falsy 吞掉
        self.start()
        row=self.row('z1'); row['load_kg']=0; row['rpe']=10
        sug=self.log(row)['suggestion']
        self.assertEqual(sug['load_kg'],0.0)
    def test_suggestion_warmup_branch(self):
        # P4 回归：热身组 RPE 超标只回调热身，不驱动正式组降载
        self.start()
        row=self.row('w1'); row['kind']='warmup'; row['rpe']=9
        sug=self.log(row)['suggestion']
        self.assertEqual(sug['action'],'reduce_warmup_load')
        self.assertNotIn('rest_seconds',sug)
        row2=self.row('w2'); row2['kind']='warmup'; row2['rpe']=6
        self.assertEqual(self.log(row2)['suggestion']['action'],'observe')

if __name__=='__main__': unittest.main()

"""可运行的用户流程验证；素材为临时合成视频，不随skill分发。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'xbotgo-highlights'/'scripts'
sys.path.insert(0, str(SCRIPTS))
import match_session as ms


class SessionWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.s1 = self.root/"2026-01-01 10:00:00 '中文.mp4"
        self.s2 = self.root/'2026-01-01 10:00:03.mp4'
        for path, audio in [(self.s1, False), (self.s2, True)]:
            cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                   'testsrc2=size=320x240:rate=30:duration=3']
            if audio:
                cmd += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=3']
            cmd += ['-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p']
            if audio:
                cmd += ['-c:a', 'aac']
            subprocess.run(cmd+[str(path)], check=True, capture_output=True)
        self.session = self.root/'task'/'session.json'
        self.cli('init', str(self.s2), str(self.s1))

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS/'match_session.py'),
                                 '--session', str(self.session), *args], capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def data(self):
        return json.loads(self.session.read_text())

    def add_ref(self, source='F01'):
        self.cli('reference', '--source', source, '--at', '1', '--end', 'A',
                 '--box', '.1', '.2', '.2', '.3', '--label', '比赛门 </script> & 中文')

    def approve(self, data=None, box=None, status='approved'):
        data = data or self.data()
        result = dict(snapshot=ms.reference_snapshot(data), note='用户已审核两端比赛门',
                      references=[dict(id=r['id'], box=box or r['box'], status=status)
                                  for r in data['goal_references']])
        path = self.root/'review.json'
        path.write_text(json.dumps(result))
        return path

    def event(self, *extra, ok=True):
        return self.cli('event', '--source', 'F01', '--at', '1.5', '--kind', 'shot',
                        '--goal-ref', 'G001', '--reason', '已查看连续画面',
                        '--before', '.5', '--after', '.5', *extra, ok=ok)

    def test_approval_progress_and_staleness(self):
        self.assertEqual(self.data()['sources'][0]['file'], str(self.s1.resolve()))
        self.add_ref()
        self.event('--status', 'confirmed', ok=False)
        self.event('--goal-ref', 'unknown')  # 定位未知仍保留候选
        self.cli('goals-page')
        page = (self.session.parent/'goal_review.html').read_text()
        self.assertIn('\\u003c/script>', page)
        old = self.data()
        review = self.approve(old)
        self.cli('coverage', '--source', 'F01', '--start', '0', '--end', '1', '--note', '已看')
        self.cli('import-goals', '--file', str(review))  # 进度变化不使审核过期
        self.event('--status', 'confirmed')
        self.event('--goal-status', 'confirmed', '--status', 'confirmed', ok=False)
        self.add_ref('F02')
        self.cli('import-goals', '--file', str(review), ok=False)
        review = self.approve(box=[.2, .2, .2, .3])
        self.cli('import-goals', '--file', str(review))
        self.assertTrue(all(e['status'] == 'uncertain' for e in self.data()['events']))
        self.cli('init', str(self.s1), str(self.s2))
        self.assertEqual(len(self.data()['events']), 2)
        self.cli('reference', '--source', 'F01', '--at', 'nan', '--end', 'B',
                 '--box', '0', '0', '.1', '.1', '--label', '非法', ok=False)
        st = self.s1.stat(); os.utime(self.s1, ns=(st.st_atime_ns, st.st_mtime_ns+1000000))
        self.cli('status', ok=False)

    def test_export_filters_overlap_audio_and_resume(self):
        self.add_ref(); self.add_ref('F02')
        self.cli('import-goals', '--file', str(self.approve()))
        self.event('--status', 'confirmed')  # 已确认射门，不是进球
        self.event('--kind', 'goal')  # 疑似不进入主片
        self.event('--kind', 'goal', '--status', 'confirmed', '--goal-status', 'confirmed',
                   '--goal-evidence', '合成测试用标签，不代表实际视觉识别')
        self.cli('event', '--source', 'F02', '--at', '1.5', '--kind', 'goal',
                 '--status', 'confirmed', '--goal-status', 'confirmed', '--goal-ref', 'G002',
                 '--reason', '测试记录', '--goal-evidence', '测试标签', '--before', '.5', '--after', '.5')
        out = self.root/'goals'
        self.cli('export', '--collection', 'goals', '--out-dir', str(out), ok=False)
        # 重叠查看范围不重复累计，漏查第二段时仍阻止常规导出。
        for start, end in [('0', '2'), ('1', '3')]:
            self.cli('coverage', '--source', 'F01', '--start', start, '--end', end, '--note', '合成验证')
        report = json.loads(self.cli('status').stdout)
        self.assertEqual(report[0]['reviewed_seconds'], 3)
        self.assertEqual(report[1]['unreviewed_ranges'], [[0, 3.0]])
        self.cli('export', '--collection', 'goals', '--out-dir', str(out), ok=False)
        self.cli('coverage', '--source', 'F02', '--start', '0', '--end', '3', '--note', '合成验证')
        self.cli('preview')
        cache = list((self.session.parent/'cache').glob('*.mp4'))
        mtimes = {str(p): p.stat().st_mtime_ns for p in cache}
        self.cli('preview')
        self.assertEqual(mtimes, {str(p): p.stat().st_mtime_ns for p in cache})
        self.cli('export', '--collection', 'goals', '--out-dir', str(out))
        result = json.loads((out/'export.json').read_text())
        self.assertEqual(len(result['events']), 2)
        self.assertEqual([g['source'] for g in result['segments']], ['F01', 'F02'])
        self.assertTrue(result['decode_ok']); self.assertFalse(result['partial'])
        self.assertEqual({s['codec_type'] for s in result['output_probe']['streams']}, {'audio', 'video'})
        self.assertLess(abs(float(result['output_probe']['format']['duration'])-2), .15)
        self.cli('export', '--collection', 'goals', '--out-dir', str(out), ok=False)
        allout = self.root/'all'
        self.cli('export', '--collection', 'all', '--out-dir', str(allout))
        allresult = json.loads((allout/'export.json').read_text())
        self.assertEqual(len(allresult['events']), 3)
        self.assertEqual(len(allresult['segments']), 2)  # F01重叠动作只剪一次
        self.cli('export', '--collection', 'goals', '--status', 'uncertain', '--out-dir', str(self.root/'pending'))
        pending = json.loads((self.root/'pending'/'export.json').read_text())
        self.assertEqual([e['id'] for e in pending['events']], ['E0002'])


if __name__ == '__main__':
    unittest.main()

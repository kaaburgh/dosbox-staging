import csv,json,struct,subprocess,tempfile,unittest
from pathlib import Path
PARSER=str(Path(__file__).resolve().parent.parent)
SCRIPT=str(Path(__file__).resolve().parent/'analyze_helper_probe.py')
FIELDS=['event','monotonic_ns','pid','generation','guest_cs','guest_eip','guest_linear_begin','guest_linear_end','host_begin','host_end']
class JoinTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.run=self.root/'run';self.run.mkdir()
  (self.run/'result.json').write_text(json.dumps({'pid':17,'result':{'frames':969},'demo_start_observed_ns':1,'demo_end_observed_ns':99}))
  (self.run/'dosbox.log').write_text('ok\n')
  with (self.run/'jit-blocks.csv').open('w',newline='') as f:
   w=csv.writer(f);w.writerow(FIELDS)
   w.writerows([['C',10,17,1,'0x1','0x10','0x1000','0x1010','0x2000','0x2100'],['D',30,17,1,'0','0','0','0','0','0'],['C',40,17,2,'0x1','0x20','0x2000','0x2010','0x2000','0x2100']])
  self.probe=self.root/'probe.bin';self.out=self.root/'out.json'
 def tearDown(self): self.tmp.cleanup()
 def run_join(self):return subprocess.run(['python3',SCRIPT,'--parser-dir',PARSER,'--run',str(self.run),'--probe',str(self.probe),'--out',str(self.out),'--stride','1'],capture_output=True,text=True)
 def test_reuse_and_return_end(self):
  self.probe.write_bytes(b''.join(struct.pack('<QQQIIQ',t,r,s,0x3000,0,0) for t,r,s in [(20,0x2100,1),(35,0x2001,2),(45,0x2001,3)]))
  r=self.run_join();self.assertEqual(r.returncode,0,r.stderr);d=json.loads(self.out.read_text());self.assertEqual(d['unmatched'],{'fpu_load32':1});self.assertEqual({x['guest_eip'] for x in d['blocks']},{'0x00000010','0x00000020'})
 def test_truncated(self):
  self.probe.write_bytes(b'bad');self.assertNotEqual(self.run_join().returncode,0)
 def test_recorder_error(self):
  self.probe.write_bytes(b'');(self.run/'dosbox.log').write_text('JIT helper probe: write failed');self.assertNotEqual(self.run_join().returncode,0)
 def test_pid_mismatch(self):
  self.probe.write_bytes(b'');(self.run/'result.json').write_text(json.dumps({'pid':18,'result':{'frames':969},'demo_start_observed_ns':1,'demo_end_observed_ns':99}));self.assertNotEqual(self.run_join().returncode,0)
if __name__=='__main__':unittest.main()

import json,runpy,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
class DeadlineTests(unittest.TestCase):
 def test_expired_overall_budget_stops_before_host_or_guard(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);target=root/'verify_deployment.py'
   target.write_bytes(Path(__file__).with_name('verify_deployment.py').read_bytes())
   (root/'result.json').write_text(json.dumps({'status':'fix_deployed_open'}))
   (root/'dispatch-once.json').write_text(json.dumps({'deadline_epoch':time.time()-1}))
   module=types.ModuleType('inspect_isluno_target');module.bounded_command=Mock();module.ssh_argv=Mock()
   with patch.dict(sys.modules,{'inspect_isluno_target':module}):
    with self.assertRaisesRegex(AssertionError,'postverification_overall_budget_unavailable'):runpy.run_path(str(target))
   module.bounded_command.assert_not_called();module.ssh_argv.assert_not_called()
   self.assertFalse((root/'verification-once').exists())
if __name__=='__main__':unittest.main()

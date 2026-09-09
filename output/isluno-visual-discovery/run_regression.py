import json,unittest,time
from pathlib import Path
from test_hospitality import HospitalityTests
HospitalityTests.records=[]
HospitalityTests.tearDownClass=classmethod(lambda cls:None)
modules=['test_visual_discovery.VisualDiscoveryTests','test_communication_wire.CommunicationWireTests','test_no_reply.NoReplyTests','test_presentation_tolerance','test_conversation.ConversationTests','test_discovery.DiscoveryTests','test_quotes.QuoteTests','test_payments','test_recovery.RecoveryTests','test_hospitality_delivery.HospitalityDeliveryTests','test_hospitality.HospitalityTests']
suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
started=time.monotonic();result=unittest.TextTestRunner(verbosity=1).run(suite)
root=Path('output/isluno-visual-discovery')
(root/'TESTS.json').write_text(json.dumps({'evidence':'Network-disabled Docker; scripted model SDK and provider HTTP substitutes. No paid providers or customer sends.','source_commit':'0361641575552e5a78a671efbc1dd59b9d5196ba','tests_run':result.testsRun,'elapsed_seconds':time.monotonic()-started,'failures':[{'test':str(t),'detail':d} for t,d in result.failures],'errors':[{'test':str(t),'detail':d} for t,d in result.errors],'success':result.wasSuccessful()},indent=2))
(root/'conversations.json').write_text(json.dumps({'evidence':'Synthetic catalog, scripted model responses, stub transports. No live model or native-language quality proof.','conversations':HospitalityTests.records},ensure_ascii=False,indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)

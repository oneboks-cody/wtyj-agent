"""Read-only Today projection from real synthetic journey/fulfillment state."""
import unittest
import test_operations as operations
from test_conversation import response
class TodayTests(unittest.TestCase):
    def test_pending_failed_handover_and_demo_totals_are_server_owned(self):
        t=operations.OperationsTests('test_reads_do_not_modify_snapshots_or_create_delivery');t.setUp();self.addCleanup(t.doCleanups)
        paid=t.pay(t.approved(multi=True));t.deliver(paid,['accepted','accepted','ambiguous'])
        t.turn(response('human'))
        before=t.payments.records(t.scope())
        result=t.get('today');self.assertEqual(result.status_code,200);self.assertEqual(result.headers['cache-control'],'no-store')
        data=result.json();self.assertEqual(data['counts']['itineraries'],1);self.assertEqual(data['counts']['trip_items'],2);self.assertEqual(data['counts']['demo_paid'],1)
        self.assertEqual(data['counts']['attention'],len(data['attention']));self.assertTrue(any(a['kind']=='handover' and a['status']=='pending' for a in data['attention']))
        self.assertTrue(any(a['kind']=='delivery' and a['status']=='ambiguous' for a in data['attention']))
        self.assertTrue(any(a['status']=='queued' for a in data['attention']))
        self.assertFalse(any(a['status']=='accepted' for a in data['attention']))
        self.assertEqual(data['availability'],'assumed_demo');self.assertEqual(data['payment'],'simulated');self.assertFalse(data['supplier_booking_made'])
        self.assertEqual(t.payments.records(t.scope()),before)
        self.assertEqual(t.client.get('/operations/today').status_code,401)
        config=dict(t.config);config['features']={};t.write_config(config);self.assertEqual(t.get('today').status_code,403)
    def test_today_is_empty_only_when_records_are_empty(self):
        t=operations.OperationsTests('test_reads_do_not_modify_snapshots_or_create_delivery');t.setUp();self.addCleanup(t.doCleanups)
        data=t.get('today').json();self.assertEqual(data['counts'],{'itineraries':0,'trip_items':0,'demo_paid':0,'attention':0});self.assertEqual(data['scheduled_today'],[])

    def test_scheduled_today_uses_the_trip_timezone(self):
        from datetime import datetime,timezone
        t=operations.OperationsTests('test_reads_do_not_modify_snapshots_or_create_delivery');t.setUp();self.addCleanup(t.doCleanups)
        t.initial()
        t.itinerary.clock=lambda:datetime(2026,10,15,1,tzinfo=timezone.utc)
        self.assertEqual(t.get('today').json()['scheduled_today'],[])
        t.itinerary.clock=lambda:datetime(2026,10,15,5,tzinfo=timezone.utc)
        rows=t.get('today').json()['scheduled_today'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['timezone'],'America/Curacao')

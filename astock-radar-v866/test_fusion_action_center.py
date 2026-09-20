import unittest
from fusion_action_center import build_action_center


class ActionCenterTests(unittest.TestCase):
    def test_deduplicates_and_limits_low_pool_without_execution(self):
        base={'ts_code':'A','name':'甲','state':'WATCH','family':'LATENT_BASE','evidence':{'structure':{'position60':.2,'volume_contraction':.8}}}
        result=build_action_center([base],{'candidates':[dict(base,state='LOW_POSITION_WATCH')]},{'status':'NO_NEW_VERSION_POSITIONS'})
        self.assertEqual(result['low_position_count'],1)
        self.assertEqual(result['actionable_count'],0)
        self.assertFalse(result['execution_eligible'])

    def test_only_confirmed_pullback_is_called_possible_washout(self):
        row={'ts_code':'A','state':'WATCH','family':'TREND_PULLBACK','matched_families':['TREND_PULLBACK'],
             'price':10,'evidence':{'structure':{'support':9},'pullback_sequence':{'confirmed':True}}}
        result=build_action_center([row],{}, {})
        self.assertEqual(result['pullback_count'],1)
        self.assertFalse(result['possible_washout'][0]['intent_confirmed'])

    def test_plain_decline_is_not_labeled_washout_and_breakdown_is_visible(self):
        row={'ts_code':'A','state':'NO_SETUP','price':8,'evidence':{'structure':{'support':9},'pullback_sequence':{'confirmed':False}}}
        result=build_action_center([row],{}, {})
        self.assertEqual(result['pullback_count'],0)
        self.assertEqual(result['breakdown_count'],1)

    def test_missing_positions_are_disclosed(self):
        result=build_action_center([],{}, {'status':'NO_NEW_VERSION_POSITIONS','items':[]})
        self.assertEqual(result['portfolio_status'],'NO_NEW_VERSION_POSITIONS')
        self.assertEqual(result['portfolio_items'],[])


if __name__=='__main__': unittest.main()

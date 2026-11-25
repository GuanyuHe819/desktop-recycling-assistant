import importlib.util
from unittest.mock import patch
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('client', ROOT/'computer'/'app.py')
client = importlib.util.module_from_spec(spec);spec.loader.exec_module(client)

class ModelInputTests(unittest.TestCase):
    def test_labels_strip_indices_and_match_by_name(self):
        with patch.object(Path, 'read_text', return_value='0 metal\n1 empty\n2 paper\n3 plastic\n'):
            names=client.read_labels('labels.txt')
        client.check_labels(names,['paper','plastic','metal'])
        self.assertEqual(names[0],'metal')
    def test_wrong_labels_are_rejected(self):
        with self.assertRaises(ValueError): client.check_labels(['paper','plastic','empty'], ['paper','plastic','metal'])
    def test_stability_requires_consecutive_confident_frames(self):
        s=client.Stable(3,.85)
        self.assertIsNone(s.add('paper',.9)); self.assertIsNone(s.add('paper',.5))
        self.assertIsNone(s.add('paper',.9)); self.assertIsNone(s.add('paper',.9))
        self.assertEqual(s.add('paper',.9),'paper')
        self.assertIsNone(s.add('empty',.99))
if __name__=='__main__': unittest.main()

from __future__ import print_function, division
import unittest

class KnowValues(unittest.TestCase):

  def test_water_pp_pb(self):
    """ This is for initializing with SIESTA radial orbitals """
    from pyscf.nao import system_vars_c
    import os
    dname = os.path.dirname(os.path.abspath(__file__))
    sv = system_vars_c().init_siesta_xml(chdir=dname)
    self.assertEqual(sv.norbs, 23)
    

if __name__ == "__main__":
  unittest.main()

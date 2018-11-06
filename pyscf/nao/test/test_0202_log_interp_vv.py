from __future__ import print_function, division
import unittest, numpy as np

from pyscf.nao.log_mesh import funct_log_mesh
from pyscf.nao.m_log_interp import log_interp_c

class KnowValues(unittest.TestCase):

  def test_log_interp_vv_speed(self):
    """ Test the interpolation facility for an array arguments from the class log_interp_c """
    rr,pp = funct_log_mesh(1024, 0.01, 200.0)
    lgi = log_interp_c(rr)

    gcs = np.array([1.2030, 3.2030, 0.7, 10.0])
    ff = np.array([[np.exp(-gc*r**2) for r in rr] for gc in gcs])

    rr = np.linspace(0.05, 250.0, 2000000)
    fr2yy = lgi(ff, rr, rcut=16.0)
    yyref = np.exp(-(gcs.reshape(gcs.size,1)) * (rr.reshape(1,rr.size)**2))
      
    self.assertTrue(np.allclose(fr2yy, yyref) )    

if __name__ == "__main__": unittest.main()

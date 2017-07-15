from __future__ import print_function, division
import os,unittest,numpy as np

class KnowValues(unittest.TestCase):

  def test_dens_elec(self):
    """ Compute density in coordinate space with system_vars_c, integrate and compare with number of electrons """
    from pyscf.nao import system_vars_c
    from pyscf.nao.m_comp_dm import comp_dm
    from pyscf.nao.m_fermi_dirac import fermi_dirac_occupations
    from timeit import default_timer as timer
    
    sv = system_vars_c().init_siesta_xml(label='water', cd=os.path.dirname(os.path.abspath(__file__)))
    ksn2fd = fermi_dirac_occupations(sv.hsx.telec, sv.wfsx.ksn2e, sv.fermi_energy)
    ksn2f = (3-sv.nspin)*ksn2fd
    dm = comp_dm(sv.wfsx.x, ksn2f)
    grid = sv.build_3dgrid(level=9)
    
    
    t1 = timer()
    dens = sv.dens_elec(grid.coords, dm)
    #t2 = timer(); print(t2-t1); t1 = timer()
    
    nelec = np.einsum("is,i", dens, grid.weights)
    #t2 = timer(); print(t2-t1, nelec, dens.shape); t1 = timer()

    self.assertTrue(abs(nelec-sv.hsx.nelec)<1e-2)
      
if __name__ == "__main__": unittest.main()

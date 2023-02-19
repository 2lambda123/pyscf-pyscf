import unittest
import numpy
from pyscf.pbc import gto, dft
from pyscf.pbc.dft import multigrid
from pyscf.pbc.grad import rks as rks_grad
from pyscf.pbc.grad import uks as uks_grad
from pyscf.pbc.grad import krks as krks_grad

def setUpModule():
    global cell
    cell = gto.Cell()
    boxlen = 5.0
    cell.a = numpy.array([[boxlen,0.0,0.0],
                          [0.0,boxlen,0.0],
                          [0.0,0.0,boxlen]])
    cell.atom = """
        O          1.84560        1.21649        1.10372
        H          2.30941        1.30070        1.92953
        H          0.91429        1.26674        1.28886
    """
    cell.basis = 'gth-szv'
    cell.ke_cutoff = 200  # in a.u.
    cell.precision = 1e-8
    cell.pseudo = 'gth-pade'
    cell.verbose = 0
    cell.rcut_by_shell_radius = True
    cell.build()

def tearDownModule():
    global cell
    del cell

def _fftdf_energy_force(cell, xc):
    mf = dft.KRKS(cell, kpts=numpy.zeros((1,3)))
    mf.xc = xc
    e = mf.kernel()
    grad = krks_grad.Gradients(mf)
    g = grad.kernel()
    return e, g

def _multigrid2_energy_grad(cell, xc, spin=0):
    if spin == 0:
        mf = dft.RKS(cell)
    elif spin == 1:
        mf = dft.UKS(cell)
    mf.xc =  xc
    mf.with_df = multigrid.MultiGridFFTDF2(cell)
    e = mf.kernel()
    if spin == 0:
        g = rks_grad.Gradients(mf).kernel()
    elif spin == 1:
        g = uks_grad.Gradients(mf).kernel()
    return e, g

class KnownValues(unittest.TestCase):
    def test_orth_lda(self):
        xc = 'lda, vwn'
        e0, g0 = _fftdf_energy_force(cell, xc)
        e, g = _multigrid2_energy_grad(cell, xc, 0)
        e1, g1 = _multigrid2_energy_grad(cell, xc, 1)
        assert abs(e-e0) < 1e-8
        assert abs(e1-e0) < 1e-8
        assert abs(g-g0).max() < 2e-5
        assert abs(g1-g0).max() < 2e-5

    def test_orth_rks_gga(self):
        xc = 'pbe, pbe'
        e0, g0 = _fftdf_energy_force(cell, xc)
        e, g = _multigrid2_energy_grad(cell, xc, 0)
        e1, g1 = _multigrid2_energy_grad(cell, xc, 1)
        assert abs(e-e0) < 1e-6
        assert abs(e1-e0) < 1e-6
        assert abs(g-g0).max() < 2e-4
        assert abs(g1-g0).max() < 2e-4

if __name__ == '__main__':
    print("Full Tests for multigrid2")
    unittest.main()
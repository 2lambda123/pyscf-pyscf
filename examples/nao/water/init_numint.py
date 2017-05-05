from __future__ import print_function, division
from pyscf.nao.m_system_vars import system_vars_c, diag_check, overlap_check
import numpy as np
import time
from pyscf import gto
from pyscf import dft
from pyscf.dft.numint import nr_vxc

mol = gto.M(atom='H 0 0 -0.505; H 0 0 0.505', basis='ccpvdz')
mf = dft.RKS(mol)
mf.grids.atom_grid = {"H": (30, 194), "O": (30, 194),},
mf.grids.prune = None
mf.grids.build()
dm = mf.get_init_guess()

np.random.seed(1)
dm1 = np.random.random((dm.shape))
print(time.clock())
res = mf._numint.nr_vxc(mol, mf.grids, mf.xc, dm1, spin=0)
print(res)


sv  = system_vars_c(label='siesta')
print(diag_check(sv))
print(overlap_check(sv))

grids = dft.gen_grid.Grids(sv)
grids.level = 4
grids.build()
print(grids.weights.shape, grids.coords.shape)

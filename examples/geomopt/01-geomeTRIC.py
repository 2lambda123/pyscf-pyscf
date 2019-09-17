#!/usr/bin/env python

'''
Use geomeTRIC library to optimize the molecular geometry.
'''

from pyscf import gto, scf
from pyscf.geomopt.geometric_solver import optimize

mol = gto.M(atom='N 0 0 0; N 0 0 1.2', basis='ccpvdz')
mf = scf.RHF(mol)

#
# geometry optimization for HF
#
mol_eq = optimize(mf)
print(mol_eq.atom_coords())

#
# geometry optimization for CASSCF
#
from pyscf import mcscf
mf = scf.RHF(mol)
mc = mcscf.CASSCF(mf, 4, 4)
conv_params = {
    'convergence_energy': 1e-4,  # Eh
    'convergence_grms': 3e-3,    # Eh/Bohr
    'convergence_gmax': 4.5e-3,  # Eh/Bohr
    'convergence_drms': 1.2e-2,  # Angstrom
    'convergence_dmax': 1.8e-2,  # Angstrom
}
mol_eq = optimize(mc, **conv_params)


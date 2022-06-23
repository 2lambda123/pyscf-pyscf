#!/usr/bin/env python
# Copyright 2014-2022 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Author: Matthew Hennefarth <matthew.hennefarth@gmail.com>

import numpy as np

from pyscf import data
from pyscf import __config__

#PYSCF_SEED = getattr(__config__, 'seed', False)




def MaxwellBoltzmannVelocity(mol, T=298.15, seed=None):
    veloc = []
    Tkb = T*data.nist.BOLTZMANN/data.nist.HARTREE2J
    MEAN = 0.0

    rng = np.random.default_rng(seed=seed)

    for m in mol.atom_charges():
        m = data.elements.COMMON_ISOTOPE_MASSES[m]
        arg = Tkb/m
        sigma = np.sqrt(arg)

        veloc.append(rng.normal(loc=MEAN, scale=sigma, size=(3)))

    return np.array(veloc)


if __name__ == "__main__":
    from pyscf import gto

    h2o = gto.M(verbose=3,
                output='/dev/null',
                atom=[['O', 0, 0, 0], ['H', 0, -0.757, 0.587],
                      ['H', 0, 0.757, 0.587]],
                basis='def2-svp')

    print(MaxwellBoltzmannVelocity(h2o))
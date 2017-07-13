from __future__ import print_function, division
from pyscf.nao.m_overlap_ni import overlap_ni
import numpy as np

def comp_overlap_coo(sv, ao_log=None, funct=overlap_ni,**kvargs):
    """
    Computes the overlap matrix and returns it in coo format (simplest sparse format to construct)
    Args:
    sv : (System Variables), this must have arrays of coordinates and species, etc
    Returns:
    overlap (real-space overlap) for the whole system
    """
    from pyscf.nao.m_ao_matelem import ao_matelem_c
    from scipy.sparse import coo_matrix
    from numpy import zeros

    me = ao_matelem_c(sv.ao_log) if ao_log is None else ao_matelem_c(ao_log)
    atom2s = zeros((sv.natm+1), dtype=np.int32)
    for atom,sp in enumerate(sv.atom2sp): atom2s[atom+1]=atom2s[atom]+me.ao1.sp2norbs[sp]
    sp2rcut = np.array([max(mu2rcut) for mu2rcut in me.ao1.sp_mu2rcut])

    nnz = 0
    for sp1,rv1 in zip(sv.atom2sp,sv.atom2coord):
        n1,rc1 = me.ao1.sp2norbs[sp1],sp2rcut[sp1]
        for sp2,rv2 in zip(sv.atom2sp,sv.atom2coord):
            if (rc1+sp2rcut[sp2])**2>((rv1-rv2)**2).sum() : nnz = nnz + n1*me.ao1.sp2norbs[sp2]

    irow,icol,data = zeros(nnz, dtype=np.int64),zeros(nnz, dtype=np.int64), zeros(nnz, dtype=np.float64) # Start to construct coo matrix

    inz=-1
    for atom1,[sp1,rv1,s1,f1] in enumerate(zip(sv.atom2sp,sv.atom2coord,atom2s,atom2s[1:])):
        for atom2,[sp2,rv2,s2,f2] in enumerate(zip(sv.atom2sp,sv.atom2coord,atom2s,atom2s[1:])):
            if (sp2rcut[sp1]+sp2rcut[sp2])**2<=sum((rv1-rv2)**2) : continue
            oo = funct(me,sp1,rv1,sp2,rv2,**kvargs)
            for o1 in range(s1,f1):
                for o2 in range(s2,f2):
                    inz = inz+1
                    irow[inz],icol[inz],data[inz] = o1,o2,oo[o1-s1,o2-s2]

    norbs = atom2s[-1]
    return coo_matrix((data, (irow, icol)), shape=(norbs, norbs))
#
#
#
if __name__=='__main__':
  from pyscf.nao.m_comp_overlap_coo import comp_overlap_coo
  from pyscf.nao.m_system_vars import system_vars_c
  from pyscf.nao.m_overlap_am import overlap_am
  from pyscf.nao.m_overlap_ni import overlap_ni

  sv = system_vars_c(label='siesta')
  over = comp_overlap_coo(sv, funct=overlap_ni, level=7).tocsr()
  
  diff = (sv.hsx.s4_csr-over).sum()
  summ = (sv.hsx.s4_csr+over).sum()
  print(diff/summ, diff/over.size)

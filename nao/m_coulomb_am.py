from __future__ import division, print_function
import numpy as np
from pyscf.nao.m_csphar import csphar
from pyscf.nao.m_log_interp import comp_coeffs
from pyscf.nao.m_xjl import xjl

#
#
#
def coulomb_am(self, sp1, sp2, R1, R2):
  """
    Computes Coulomb overlap for an atom pair. The atom pair is given by a pair of species indices and the coordinates of the atoms.
    <a|r^-1|b> = \iint a(r)|r-r'|b(r')  dr dr'
    Args: 
      self: class instance of ao_matelem_c
      sp1,sp2 : specie indices, and
      R1,R2 :   respective coordinates
    Result:
      matrix of Coulomb overlaps
    The procedure uses the angular momentum algebra and spherical Bessel transform. It is almost a repetition of bilocal overlaps.
  """

  shape = [self.sp2norbs[sp] for sp in (sp1,sp2)]
  oo2co = np.zeros(shape)
  R2mR1 = np.array(R2)-np.array(R1)
  ylm = csphar( R2mR1, 2*self.jmx+1 )
  dist = np.sqrt(sum(R2mR1*R2mR1))
  cS = np.zeros((self.jmx*2+1,self.jmx*2+1), dtype='complex128')
  cmat = np.zeros((self.jmx*2+1,self.jmx*2+1), dtype='complex128')
  rS = np.zeros((self.jmx*2+1,self.jmx*2+1), dtype='float64')

  f1f2_mom = np.zeros((self.nr), dtype='float64')
  l2S = np.zeros((2*self.jmx+1), dtype='float64')
  _j = self.jmx
  
  bessel_pp = np.zeros((_j*2+1, self.nr))
  for ip,p in enumerate(self.kk): bessel_pp[:,ip]=xjl(p*dist, _j*2)*p
  
  for mu2,l2,s2,f2 in self.sp2info[sp2]:
    for mu1,l1,s1,f1 in self.sp2info[sp1]:
      f1f2_mom = self.psi_log_mom[sp2][mu2,:] * self.psi_log_mom[sp1][mu1,:]
      l2S.fill(0.0)
      for l3 in range( abs(l1-l2), l1+l2+1):
        l2S[l3] = (f1f2_mom[:]*bessel_pp[l3,:]).sum() + f1f2_mom[0]*bessel_pp[l3,0]/self.interp_pp.dg_jt
          
      cS.fill(0.0) 
      for m1 in range(-l1,l1+1):
        for m2 in range(-l2,l2+1):
          gc,m3 = self.get_gaunt(l1,-m1,l2,m2), m2-m1
          for l3ind,l3 in enumerate(range(abs(l1-l2),l1+l2+1)):
            if abs(m3) > l3 : continue
            cS[m1+_j,m2+_j] = cS[m1+_j,m2+_j] + l2S[l3]*ylm[ l3*(l3+1)+m3] * gc[l3ind] * (-1.0)**((3*l1+l2+l3)//2+m2)
                  
      self.c2r_( l1,l2, self.jmx,cS,rS,cmat)
      oo2co[s1:f1,s2:f2] = rS[-l1+_j:l1+_j+1,-l2+_j:l2+_j+1]
      
  oo2co = oo2co * (4*np.pi)**2 * self.interp_pp.dg_jt
  return oo2co


if __name__ == '__main__':
  from pyscf.nao.m_system_vars import system_vars_c
  from pyscf.nao.m_prod_log import prod_log_c
  from pyscf.nao.m_ao_matelem import ao_matelem_c
  from pyscf.nao.m_pack2den import pack2den
  
  
  ra = np.array([0.0, 0.1, 0.2])
  rb = np.array([0.0, 0.1, 0.0])
  me = ao_matelem_c(ao_log)
  coulo = coulomb_am(me, 0, 0, ra, rb)

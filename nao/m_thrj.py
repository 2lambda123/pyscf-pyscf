from __future__ import print_function, division
import numpy as np
import scipy.misc as misc
from pyscf.nao.m_fact import fact as fac, sgn

def comp_number_of3j(lmax):
  n3j=0
  for l1 in range(lmax+1):
    for l2 in range(l1+1):
      for m2 in range(-l2,l2+1):
        for l3 in range(l2+1):
          n3j=n3j+2*l3+1
  return n3j

lmax = 20
ixxa = np.zeros(lmax+1, np.int32)
ixxb = np.zeros(lmax+1, np.int32)
ixxc = np.zeros(lmax+1, np.int32)
no3j = comp_number_of3j(lmax)
aa   = np.zeros(no3j, np.float64)

for ii in range(lmax+1):
  ixxa[ii]=ii*(ii+1)*(ii+2)*(2*ii+3)*(3*ii-1)/60
  ixxb[ii]=ii*(ii+1)*(3*ii**2+ii-1)/6
  ixxc[ii]=(ii+1)**2

ic=-1
yyx = 0
for l1 in range(lmax+1):
  for l2 in range(l1+1):
    for m2 in range(-l2,l2+1):
      for l3 in range(l2+1):
        for m3 in range(-l3,l3+1):
          m1=-m2-m3
          if l3>=l1-l2 and abs(m1)<=l1:
            lg=l1+l2+l3
            xx=fac[lg-2*l1]*fac[lg-2*l2]*fac[lg-2*l3]/fac[lg+1]
            xx=xx*fac[l3+m3]*fac[l3-m3]/(fac[l1+m1]*fac[l1-m1]*fac[l2+m2]*fac[l2-m2]) 
            itmin=max(0,l1-l2+m3)
            itmax=min(l3-l2+l1,l3+m3)
            ss=0.0
            for it in range(itmin,itmax+1):
              ss = ss + sgn[it]*fac[l3+l1-m2-it]*fac[l2+m2+it]/(fac[l3+m3-it]*fac[it+l2-l1-m3]*fac[it]*fac[l3-l2+l1-it]) 
            yyx=sgn[l2+m2]*np.sqrt(xx)*ss 
          ic=ic+1
          aa[ic]=yyx


def thrj(l1i,l2i,l3i,m1i,m2i,m3i):
  """
  Wigner3j symbol. Written by James Talman.
  """

  l1=l1i
  l2=l2i
  l3=l3i
  m1=m1i
  m2=m2i
  m3=m3i
  ph=1.0
  if l1<l2 :
     iz=l1
     l1=l2
     l2=iz
     iz=m1
     m1=m2
     m2=iz
     ph=ph*sgn[l1+l2+l3]

  if l2<l3 :
     iz=l2
     l2=l3
     l3=iz
     iz=m2
     m2=m3
     m3=iz
     ph=ph*sgn[l1+l2+l3]

  if l1<l2 :
     iz=l1
     l1=l2
     l2=iz
     iz=m1
     m1=m2
     m2=iz
     ph=ph*sgn[l1+l2+l3]

  if l1>lmax: raise RuntimeError('thrj: 3-j coefficient out of range')

  icc=ixxa[l1]+ixxb[l2]+ixxc[l2]*(l2+m2)+ixxc[l3]-l3+m3
  return ph*aa[icc-1]

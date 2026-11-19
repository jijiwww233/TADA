import type { Pose } from '../scatter/choreo'
import { platformLayout } from './motion'

const noise=(i:number,salt:number)=>{
  const v=Math.sin((i+1)*127.1+salt*311.7)*43758.5453
  return v-Math.floor(v)
}
const clamp=(v:number)=>Math.max(0,Math.min(1,v))
const mix=(a:number,b:number,t:number)=>a+(b-a)*t

/** A loose, irregular volume. Depth, not a flat opacity wash, makes its front and back legible. */
export class StarField {
  private last:number|null=null
  private travel=0
  advance(current:number,reduced:boolean){
    if(this.last!==null&&!reduced)this.travel+=Math.abs(current-this.last)
    this.last=current
  }
  pose(base:Pose,i:number,W:number,H:number,current:number,now:number,clarity:number,reduced:boolean){
    const owner=i%5,rank=Math.floor(i/5),l=platformLayout(W,H),mobile=W<=700
    const clock=reduced?0:now/1000
    // Platform neighbourhoods rotate through the volume; jitter prevents rings or a rigid shell.
    const angle=(owner-current)*Math.PI/3+(noise(i,1)-.5)*.62
    const longitude=(rank-1.4)*.55+(noise(i,2)-.5)*.32
    const section=Math.sqrt(Math.max(.16,1-longitude*longitude))
    const radius=.76+noise(i,3)*.36
    const approach=reduced?0:55*(1-Math.exp(-this.travel*.18))
    const sphericalZ=Math.min(210,-390+Math.cos(angle)*section*(420+noise(i,4)*220)*radius+approach)
    const proximity=clamp((sphericalZ+1050)/1260)
    const z=sphericalZ+clarity*20,depth=(1100-z)/1100
    const floatX=Math.sin(clock*(.16+noise(i,6)*.09)+i*2.1)*(5+noise(i,7)*5)
    const floatY=Math.cos(clock*(.13+noise(i,8)*.08)+i*1.7)*(5+noise(i,9)*6)
    const yaw=Math.sin(current*.26+i*1.9)*W*.018
    const sx=(mobile?0:W*.19)+longitude*W*(mobile?.40:.225)+yaw+floatX
    const sy=l.wheelCenter-H/2+Math.sin(angle)*H*.44*section*radius+(noise(i,10)-.5)*H*.065+floatY
    const nearWeight=clamp((proximity-.45)/.4)
    const foreground=nearWeight*nearWeight*(3-2*nearWeight)
    const farWeight=clamp((.55-proximity)/.3)
    const background=farWeight*farWeight*(3-2*farWeight)
    const opacity=mix(Math.min(.98,.24+proximity*.48+noise(i,11)*.06+foreground*.26),1,clarity)
    const dof=.05+(1-proximity)**2*6.5
    const ghost=.66-proximity*.55
    return {...base,x:sx*depth,y:sy*depth,z,
      rx:base.rx*.75+Math.sin(clock*.1+i)*.018,
      ry:base.ry*.75+Math.cos(clock*.12+i)*.025,
      scale:(.48+proximity*.68+noise(i,12)*.14)*(1-foreground*.25)*(1+background*.24)*(1+clarity*.06),opacity,dof,ghost}
  }
}

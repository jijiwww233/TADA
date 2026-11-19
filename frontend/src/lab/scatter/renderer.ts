/**
 * "虚虚实实" stage renderer (WebGL2, no dependencies).
 *
 * Why WebGL instead of DOM + backdrop-filter: the target machine has integrated graphics. Here the whole
 * frame costs one instanced draw for 16-20 cards, a 5-level dual-Kawase blur chain at ≤1/2 resolution
 * (only while some strip is still frosted) and one full-screen composite. Nothing has a CSS filter.
 *
 * - Cards: perspective quads with rounded corners (SDF), soft shadow, 1px hairline. Depth of field is a mip
 *   bias on a texture array (one layer per image), so a far card is blurrier without any extra pass.
 * - "Ghost" (round 3): per card, washes the image out towards the paper colour — desaturated, lighter, low
 *   contrast — so a far, blurred card reads as a face in a crowd, not as noise. 0 = full colour.
 * - Frosted strips (round 2, kept for the pure A / B comparison): up to 6 vertical rectangles; inside each, the
 *   composite picks a blur level from the pre-blurred chain (0 = sharp … 5 ≈ 48px), whitens it a little (tint),
 *   adds grain and a 1px seam. With no strips the blur chain is skipped entirely.
 * Renders only when told to (`draw()`); idle cost is zero.
 */

import { cardTextureCanvas } from '../intro/cardThumbnail'

export type Card = {
  x: number; y: number; z: number // px, origin = stage centre, +y down, +z towards the viewer
  rx: number; ry: number; rz: number // radians
  alpha: number
  dof: number // extra mip bias (0 = sharp)
  scale: number
  layer: number // image index
  ghost?: number // 0..1 washed out towards the paper (round 3)
}
export type Strip = { x0: number; y0: number; x1: number; y1: number; blur: number; tint: number; seam: number } // px in stage space (top-left origin)

const MAX_STRIPS = 6
const LEVELS = 5
export const FOCAL = 1100
/** 卡片纹理的固定尺寸：图片先缩到 <=1024 再按 cover 裁进这里，避免 Edge 拒绝尺寸不一致的上传。 */
const TEX_W = 480
const TEX_H = 640

const QUAD_VS = `#version 300 es
in vec2 a_q;
in vec4 a0; // x y z alpha
in vec4 a1; // rx ry rz dof
in vec4 a2; // layer kind(0 card, 1 shadow) scale ghost
uniform vec2 u_res;      // css px
uniform vec2 u_card;     // card size css px
out vec2 v_local;        // px from card centre, unscaled card space
out float v_alpha;
out float v_dof;
out float v_layer;
out float v_kind;
out float v_px;          // screen px per card px (for AA)
out float v_ghost;
void main() {
  float kind = a2.y;
  float pad = kind > 0.5 ? 46.0 : 2.0 + a1.w * 8.0;
  vec2 half_ = u_card * 0.5 + pad;
  vec2 lp = a_q * half_ * 2.0;
  v_local = lp;
  vec3 p = vec3(lp * a2.z, 0.0);
  if (kind > 0.5) p.xy += vec2(0.0, 14.0) * a2.z;
  float cx = cos(a1.x), sx = sin(a1.x), cy = cos(a1.y), sy = sin(a1.y), cz = cos(a1.z), sz = sin(a1.z);
  p = vec3(p.x * cz - p.y * sz, p.x * sz + p.y * cz, p.z);
  p = vec3(p.x * cy + p.z * sy, p.y, -p.x * sy + p.z * cy);
  p = vec3(p.x, p.y * cx - p.z * sx, p.y * sx + p.z * cx);
  p += a0.xyz;
  float w = max((${FOCAL.toFixed(1)} - p.z) / ${FOCAL.toFixed(1)}, 0.05);
  v_px = a2.z / w;
  v_alpha = a0.w;
  v_dof = a1.w;
  v_layer = a2.x;
  v_kind = kind;
  v_ghost = a2.w;
  gl_Position = vec4(p.x * 2.0 / u_res.x, -p.y * 2.0 / u_res.y, 0.0, w);
}`

const QUAD_FS = `#version 300 es
precision highp float;
precision highp sampler2DArray;
in vec2 v_local;
in float v_alpha;
in float v_dof;
in float v_layer;
in float v_kind;
in float v_px;
in float v_ghost;
uniform sampler2DArray u_tex;
uniform vec3 u_paper;
uniform vec2 u_card;
uniform float u_radius;
uniform float u_shadow;
uniform float u_loaded;
out vec4 o;
float sdRound(vec2 p, vec2 b, float r) { vec2 q = abs(p) - b + r; return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r; }
void main() {
  float d = sdRound(v_local, u_card * 0.5, u_radius);
  if (v_kind > 0.5) {
    // soft ambient shadow, like the card shadow token: wide and very light
    float a = 1.0 - smoothstep(-18.0, 30.0, d);
    // (a washed-out or very blurred card has almost no shadow: it is "far away")
    o = vec4(vec3(0.0), a * a * 0.13 * u_shadow * v_alpha * (1.0 - v_ghost) * (1.0 - min(v_dof * 0.2, 0.7)));
    return;
  }
  float aa = 0.75 / max(v_px, 0.2) + v_dof * 3.0; // blurred cards get soft edges too (bokeh, not a sharp blurry rectangle)
  float cover = 1.0 - smoothstep(-aa, aa, d);
  vec2 uv = v_local / u_card + 0.5;
  vec3 c = mix(u_paper * 0.95, texture(u_tex, vec3(uv, v_layer), v_dof).rgb, u_loaded);
  // 1px hairline just inside the edge (fades out with blur)
  float hair = (1.0 - smoothstep(0.0, 1.2 / max(v_px, 0.2), abs(d + 0.6 / max(v_px, 0.2)))) * (1.0 - min(v_dof, 1.0));
  c = mix(c, c * 0.86, hair * 0.5 * (1.0 - v_ghost));
  // ghost: desaturate, then lift towards the paper (this is what lowers the contrast)
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
  c = mix(c, vec3(l), v_ghost * 0.75);
  c = mix(c, u_paper, v_ghost * 0.6);
  float a = cover * v_alpha;
  o = vec4(c * a, a);
}`

const FS_VS = `#version 300 es
in vec2 a_q;
out vec2 v_uv;
void main() { v_uv = a_q + 0.5; gl_Position = vec4(a_q * 2.0, 0.0, 1.0); }`

const DOWN_FS = `#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_src;
uniform vec2 u_hp;
out vec4 o;
void main() {
  vec4 s = texture(u_src, v_uv) * 4.0;
  s += texture(u_src, v_uv - u_hp);
  s += texture(u_src, v_uv + u_hp);
  s += texture(u_src, v_uv + vec2(u_hp.x, -u_hp.y));
  s += texture(u_src, v_uv - vec2(u_hp.x, -u_hp.y));
  o = s / 8.0;
}`

const UP_FS = `#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_src;
uniform vec2 u_hp;
out vec4 o;
void main() {
  vec4 s = texture(u_src, v_uv + vec2(-u_hp.x * 2.0, 0.0));
  s += texture(u_src, v_uv + vec2(-u_hp.x, u_hp.y)) * 2.0;
  s += texture(u_src, v_uv + vec2(0.0, u_hp.y * 2.0));
  s += texture(u_src, v_uv + vec2(u_hp.x, u_hp.y)) * 2.0;
  s += texture(u_src, v_uv + vec2(u_hp.x * 2.0, 0.0));
  s += texture(u_src, v_uv + vec2(u_hp.x, -u_hp.y)) * 2.0;
  s += texture(u_src, v_uv + vec2(0.0, -u_hp.y * 2.0));
  s += texture(u_src, v_uv + vec2(-u_hp.x, -u_hp.y)) * 2.0;
  o = s / 12.0;
}`

const COMP_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_l0, u_l1, u_l2, u_l3, u_l4, u_l5;
uniform vec4 u_rect[${MAX_STRIPS}]; // device px, x0 y0 x1 y1 (top-left origin)
uniform vec4 u_prm[${MAX_STRIPS}];  // blur tint seam -
uniform int u_n;
uniform float u_h;     // device px height (to flip y)
uniform float u_dpr;
out vec4 o;
vec3 lvl(int k, vec2 uv) {
  if (k <= 0) return texture(u_l0, uv).rgb;
  if (k == 1) return texture(u_l1, uv).rgb;
  if (k == 2) return texture(u_l2, uv).rgb;
  if (k == 3) return texture(u_l3, uv).rgb;
  if (k == 4) return texture(u_l4, uv).rgb;
  return texture(u_l5, uv).rgb;
}
float hash(vec2 p) { p = fract(p * vec2(443.897, 441.423)); p += dot(p, p.yx + 19.19); return fract((p.x + p.y) * p.x); }
void main() {
  vec2 px = vec2(gl_FragCoord.x, u_h - gl_FragCoord.y);
  vec3 c = texture(u_l0, v_uv).rgb;
  for (int i = 0; i < ${MAX_STRIPS}; i++) {
    if (i >= u_n) break;
    vec4 r = u_rect[i];
    if (px.x < r.x || px.x >= r.z || px.y < r.y || px.y >= r.w) continue;
    vec4 p = u_prm[i];
    float b = clamp(p.x, 0.0, 5.0);
    int lo = int(floor(b));
    c = mix(lvl(lo, v_uv), lvl(min(lo + 1, 5), v_uv), fract(b));
    c = mix(c, vec3(0.958, 0.958, 0.953), p.y);                  // frosted veil (a touch darker than the page, so a strip reads on white)
    c += (hash(px) - 0.5) * 0.035 * clamp(p.y * 4.0 + b * 0.2, 0.0, 1.0); // grain only where it is frosted
    float e = 1.0 * u_dpr;
    float left = 1.0 - step(e, px.x - r.x), top = 1.0 - step(e, px.y - r.y);
    float right = 1.0 - step(e, r.z - px.x);
    c = mix(c, vec3(1.0), max(left, top) * 0.55 * p.z);          // light edge
    c = mix(c, c * 0.9, right * 0.6 * p.z);                       // seam shade
  }
  o = vec4(c, 1.0);
}`

type Target = { tex: WebGLTexture; fbo: WebGLFramebuffer; w: number; h: number }

export class ScatterRenderer {
  readonly gl: WebGL2RenderingContext
  private quadProg: WebGLProgram
  private downProg: WebGLProgram
  private upProg: WebGLProgram
  private compProg: WebGLProgram
  private quadVao: WebGLVertexArrayObject
  private fsVao: WebGLVertexArrayObject
  private inst: WebGLBuffer
  private instData = new Float32Array(0)
  private tex: WebGLTexture
  private loaded = 0
  private scene: Target | null = null
  private down: Target[] = []
  private tmp: Target[] = []
  private up: Target[] = []
  private cssW = 1
  private cssH = 1
  private dpr = 1
  cardW = 128
  cardH = 171
  radius = 10
  /** 卡片阴影的强度（0 = 不画阴影） */
  shadow = 1
  bg: [number, number, number] = [0.984, 0.984, 0.98]

  readonly canvas: HTMLCanvasElement

  /** @param first 先加载的纹理层（第一屏用到的）；不传 = 全部一起加载（电脑版原样） */
  constructor(canvas: HTMLCanvasElement, images: string[], first?: number[]) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, premultipliedAlpha: true, powerPreference: 'low-power' })
    if (!gl) throw new Error('WebGL2 unavailable')
    this.gl = gl
    this.quadProg = program(gl, QUAD_VS, QUAD_FS)
    this.downProg = program(gl, FS_VS, DOWN_FS)
    this.upProg = program(gl, FS_VS, UP_FS)
    this.compProg = program(gl, FS_VS, COMP_FS)

    const quad = gl.createBuffer()!
    gl.bindBuffer(gl.ARRAY_BUFFER, quad)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-0.5, -0.5, 0.5, -0.5, -0.5, 0.5, 0.5, 0.5]), gl.STATIC_DRAW)

    this.inst = gl.createBuffer()!
    this.quadVao = gl.createVertexArray()!
    gl.bindVertexArray(this.quadVao)
    bindAttr(gl, this.quadProg, 'a_q', quad, 2, 0, 0, 0)
    for (const [i, name] of ['a0', 'a1', 'a2'].entries()) bindAttr(gl, this.quadProg, name, this.inst, 4, 48, i * 16, 1)
    this.fsVao = gl.createVertexArray()!
    gl.bindVertexArray(this.fsVao)
    bindAttr(gl, this.compProg, 'a_q', quad, 2, 0, 0, 0)
    gl.bindVertexArray(null)

    // Texture array: one fixed-size layer per image, full mip chain (the mips are the depth-of-field blur).
    // Every layer is uploaded from a canvas cropped to TEX_W x TEX_H, so the source image dimensions never reach D3D11/Edge.
    const maxTexture = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number
    if (maxTexture < Math.max(TEX_W, TEX_H)) throw new Error(`WebGL 纹理尺寸超出限制（${maxTexture}）`)
    this.tex = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.tex)
    const levels = Math.floor(Math.log2(Math.max(TEX_W, TEX_H))) + 1
    gl.texStorage3D(gl.TEXTURE_2D_ARRAY, levels, gl.RGBA8, TEX_W, TEX_H, Math.max(images.length, 1))
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR)
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    const aniso = gl.getExtension('EXT_texture_filter_anisotropic')
    if (aniso) gl.texParameterf(gl.TEXTURE_2D_ARRAY, aniso.TEXTURE_MAX_ANISOTROPY_EXT, 4)
    const upload = (i: number, canvas: HTMLCanvasElement) => {
      gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.tex)
      // The canvas is exactly TEX_W x TEX_H; Edge/D3D11 no longer sees an image with a different intrinsic size.
      gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, i, TEX_W, TEX_H, 1, gl.RGBA, gl.UNSIGNED_BYTE, canvas)
      if (gl.getError() !== gl.NO_ERROR) throw new Error('纹理上传失败')
    }
    const load = (i: number) => cardTextureCanvas(images[i], TEX_W, TEX_H).then(canvas => { if (!this.disposed) upload(i, canvas) })
    const finish = () => {
      if (this.disposed) return
      gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.tex)
      gl.generateMipmap(gl.TEXTURE_2D_ARRAY)
      this.loaded = 1
    }
    if (!first) {
      this.ready = Promise.all(images.map((_, i) => load(i))).then(finish)
      this.rest = this.ready
      return
    }
    // 手机引导页：只先等第一屏用到的那几层；其余各层先铺一层纸色（不然没到的卡是黑块），
    // ready 之后再在后台两张两张地补，补完重新生成一次 mip。
    const head = [...new Set(first)].filter(i => i >= 0 && i < images.length)
    const tail = images.map((_, i) => i).filter(i => !head.includes(i))
    const blank = document.createElement('canvas')
    blank.width = TEX_W; blank.height = TEX_H
    const paper = blank.getContext('2d')
    if (paper) { paper.fillStyle = '#ebe7e1'; paper.fillRect(0, 0, TEX_W, TEX_H) }
    for (const i of tail) upload(i, blank)
    this.ready = Promise.all(head.map(load)).then(finish)
    this.rest = this.ready.then(async () => {
      const queue = [...tail]
      const worker = async () => {
        for (let i = queue.shift(); i !== undefined && !this.disposed; i = queue.shift()) await load(i).catch(() => {})
      }
      await Promise.all([worker(), worker()])
      finish()
    })
  }

  readonly ready!: Promise<void>
  /** 全部纹理都到齐（手机引导页的后台补齐也算在内） */
  readonly rest!: Promise<void>
  private disposed = false

  resize(cssW: number, cssH: number, dpr: number) {
    const gl = this.gl
    this.cssW = cssW; this.cssH = cssH; this.dpr = dpr
    const w = Math.max(1, Math.round(cssW * dpr)), h = Math.max(1, Math.round(cssH * dpr))
    this.canvas.width = w; this.canvas.height = h
    for (const t of [this.scene, ...this.down, ...this.tmp, ...this.up]) if (t) { gl.deleteTexture(t.tex); gl.deleteFramebuffer(t.fbo) }
    this.scene = target(gl, w, h)
    this.down = []; this.tmp = []; this.up = []
    for (let k = 1; k <= LEVELS; k++) this.down.push(target(gl, Math.max(1, w >> k), Math.max(1, h >> k)))
    for (let k = 2; k < LEVELS; k++) this.tmp.push(target(gl, Math.max(1, w >> k), Math.max(1, h >> k)))
    for (let k = 2; k <= LEVELS; k++) this.up.push(target(gl, Math.max(1, w >> 1), Math.max(1, h >> 1)))
  }

  draw(cards: Card[], strips: Strip[]) {
    const gl = this.gl
    if (!this.scene) return
    // 1. scene: far cards first; each card is preceded by its own shadow.
    const order = cards.map((_, i) => i).filter(i => cards[i].alpha > 0.002).sort((a, b) => cards[a].z - cards[b].z)
    const n = order.length * 2
    if (this.instData.length < n * 12) this.instData = new Float32Array(n * 12)
    const d = this.instData
    order.forEach((ci, j) => {
      const c = cards[ci]
      for (let k = 0; k < 2; k++) {
        const o = (j * 2 + k) * 12
        d[o] = c.x; d[o + 1] = c.y; d[o + 2] = c.z; d[o + 3] = c.alpha
        d[o + 4] = c.rx; d[o + 5] = c.ry; d[o + 6] = c.rz; d[o + 7] = c.dof
        d[o + 8] = c.layer; d[o + 9] = k === 0 ? 1 : 0; d[o + 10] = c.scale; d[o + 11] = c.ghost ?? 0
      }
    })
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.scene.fbo)
    gl.viewport(0, 0, this.scene.w, this.scene.h)
    gl.clearColor(this.bg[0], this.bg[1], this.bg[2], 1)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.useProgram(this.quadProg)
    gl.bindVertexArray(this.quadVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.inst)
    gl.bufferData(gl.ARRAY_BUFFER, d.subarray(0, n * 12), gl.DYNAMIC_DRAW)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.tex)
    uni(gl, this.quadProg, 'u_tex', 0, 'i')
    gl.uniform2f(loc(gl, this.quadProg, 'u_res'), this.cssW, this.cssH)
    gl.uniform2f(loc(gl, this.quadProg, 'u_card'), this.cardW, this.cardH)
    uni(gl, this.quadProg, 'u_radius', this.radius)
    uni(gl, this.quadProg, 'u_shadow', this.shadow)
    uni(gl, this.quadProg, 'u_loaded', this.loaded)
    gl.uniform3f(loc(gl, this.quadProg, 'u_paper'), ...this.bg)
    if (n) gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, n)
    gl.disable(gl.BLEND)

    // 2. blur chain, only as deep as the most frosted strip needs.
    const deepest = Math.min(LEVELS, Math.ceil(Math.max(0, ...strips.map(s => s.blur)) + 0.001))
    gl.bindVertexArray(this.fsVao)
    let src = this.scene
    for (let k = 1; k <= deepest; k++) { this.pass(this.downProg, src, this.down[k - 1]); src = this.down[k - 1] }
    for (let k = 2; k <= deepest; k++) {
      let s = this.down[k - 1]
      for (let j = k - 1; j >= 1; j--) {
        const dst = j === 1 ? this.up[k - 2] : this.tmp[j - 2]
        this.pass(this.upProg, s, dst)
        s = dst
      }
    }

    // 3. composite to screen.
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.useProgram(this.compProg)
    const levels = [this.scene, this.down[0], ...this.up]
    levels.forEach((t, i) => {
      gl.activeTexture(gl.TEXTURE0 + i)
      gl.bindTexture(gl.TEXTURE_2D, t.tex)
      uni(gl, this.compProg, `u_l${i}`, i, 'i')
    })
    const rect = new Float32Array(MAX_STRIPS * 4), prm = new Float32Array(MAX_STRIPS * 4)
    const dpr = this.dpr
    strips.slice(0, MAX_STRIPS).forEach((s, i) => {
      rect.set([s.x0 * dpr, s.y0 * dpr, s.x1 * dpr, s.y1 * dpr], i * 4)
      prm.set([Math.min(s.blur, deepest), s.tint, s.seam, 0], i * 4)
    })
    gl.uniform4fv(loc(gl, this.compProg, 'u_rect[0]'), rect)
    gl.uniform4fv(loc(gl, this.compProg, 'u_prm[0]'), prm)
    uni(gl, this.compProg, 'u_n', Math.min(strips.length, MAX_STRIPS), 'i')
    uni(gl, this.compProg, 'u_h', this.canvas.height)
    uni(gl, this.compProg, 'u_dpr', dpr)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  private pass(prog: WebGLProgram, src: Target, dst: Target) {
    const gl = this.gl
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fbo)
    gl.viewport(0, 0, dst.w, dst.h)
    gl.useProgram(prog)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, src.tex)
    uni(gl, prog, 'u_src', 0, 'i')
    gl.uniform2f(loc(gl, prog, 'u_hp'), 1 / src.w, 1 / src.h)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  dispose() {
    this.disposed = true
    const gl = this.gl
    for (const t of [this.scene, ...this.down, ...this.tmp, ...this.up]) if (t) { gl.deleteTexture(t.tex); gl.deleteFramebuffer(t.fbo) }
    gl.deleteTexture(this.tex)
    gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
}

// --- small GL helpers ---

const locCache = new WeakMap<WebGLProgram, Map<string, WebGLUniformLocation | null>>()
function loc(gl: WebGL2RenderingContext, p: WebGLProgram, name: string) {
  let m = locCache.get(p)
  if (!m) locCache.set(p, (m = new Map()))
  if (!m.has(name)) m.set(name, gl.getUniformLocation(p, name))
  return m.get(name)!
}
function uni(gl: WebGL2RenderingContext, p: WebGLProgram, name: string, v: number, t: 'f' | 'i' = 'f') {
  const l = loc(gl, p, name)
  if (t === 'i') gl.uniform1i(l, v)
  else gl.uniform1f(l, v)
}
function shader(gl: WebGL2RenderingContext, type: number, src: string) {
  const s = gl.createShader(type)!
  gl.shaderSource(s, src)
  gl.compileShader(s)
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
  return s
}
function program(gl: WebGL2RenderingContext, vs: string, fs: string) {
  const p = gl.createProgram()!
  gl.attachShader(p, shader(gl, gl.VERTEX_SHADER, vs))
  gl.attachShader(p, shader(gl, gl.FRAGMENT_SHADER, fs))
  gl.bindAttribLocation(p, 0, 'a_q')
  gl.linkProgram(p)
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) ?? 'link')
  return p
}
function bindAttr(gl: WebGL2RenderingContext, p: WebGLProgram, name: string, buf: WebGLBuffer, size: number, stride: number, offset: number, divisor: number) {
  const l = gl.getAttribLocation(p, name)
  if (l < 0) return
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.enableVertexAttribArray(l)
  gl.vertexAttribPointer(l, size, gl.FLOAT, false, stride, offset)
  gl.vertexAttribDivisor(l, divisor)
}
function target(gl: WebGL2RenderingContext, w: number, h: number): Target {
  const tex = gl.createTexture()!
  gl.bindTexture(gl.TEXTURE_2D, tex)
  gl.texStorage2D(gl.TEXTURE_2D, 1, gl.RGBA8, w, h)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  const fbo = gl.createFramebuffer()!
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0)
  return { tex, fbo, w, h }
}

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

/** Secondary copy: a short focus, never an ink reveal. Change the key to replay only changed copy. */
export function SoftText({ children, delay = 0, className = '' }: { children: ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect() }
    })
    if (ref.current) observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return <span ref={ref} className={`intro-soft-text ${className}`} style={{ '--soft-delay': `${delay}ms`, animationPlayState: visible ? 'running' : 'paused' } as CSSProperties}>{children}</span>
}

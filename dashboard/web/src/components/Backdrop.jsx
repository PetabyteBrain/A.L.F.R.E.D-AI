import { useEffect, useRef, useState } from "react";
import { useThemeName } from "../theme.js";
import { backdropFor } from "../backdrops.js";

const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function Backdrop() {
  const theme = useThemeName();
  const canvasRef = useRef(null);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    const size = { w: 0, h: 0 };
    const pointer = { x: -9999, y: -9999 };
    const still = reduceMotion();
    let draw = () => {};
    const setup = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      size.w = window.innerWidth; size.h = window.innerHeight;
      canvas.width = size.w * dpr; canvas.height = size.h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      draw = backdropFor(theme, ctx, size, pointer);
      draw(false);
    };
    setup();
    let raf = 0, last = 0;
    const loop = (t) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden || t - last < 33) return; // ~30 fps is plenty for ambience
      last = t;
      draw(true);
    };
    if (!still) raf = requestAnimationFrame(loop);
    const onMove = (e) => { pointer.x = e.clientX; pointer.y = e.clientY; };
    window.addEventListener("resize", setup);
    window.addEventListener("pointermove", onMove, { passive: true });

    // Occasional lightning in the Batcave.
    let timer = 0;
    if (theme === "batcave" && !still) {
      const strike = () => {
        setFlash(true);
        setTimeout(() => setFlash(false), 900);
        timer = setTimeout(strike, 14000 + Math.random() * 20000);
      };
      timer = setTimeout(strike, 6000 + Math.random() * 8000);
    }
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      window.removeEventListener("resize", setup);
      window.removeEventListener("pointermove", onMove);
    };
  }, [theme]);

  return (
    <div className="backdrop" aria-hidden="true">
      <canvas ref={canvasRef} />
      <div className="floor" />
      <div className={`flash${flash ? " on" : ""}`} />
    </div>
  );
}

import { useRef, useMemo } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";

/* Perlin-like noise for terrain height */
function noise(x, y) {
  const X = Math.floor(x) & 255;
  const Y = Math.floor(y) & 255;
  return (Math.sin(X * 127.1 + Y * 311.7) * 43758.5453) % 1;
}
function smoothNoise(x, y) {
  const fx = x - Math.floor(x);
  const fy = y - Math.floor(y);
  const ux = fx * fx * (3 - 2 * fx);
  const uy = fy * fy * (3 - 2 * fy);
  const a = noise(Math.floor(x), Math.floor(y));
  const b = noise(Math.floor(x) + 1, Math.floor(y));
  const c = noise(Math.floor(x), Math.floor(y) + 1);
  const d = noise(Math.floor(x) + 1, Math.floor(y) + 1);
  return a + (b - a) * ux + (c - a) * uy + (d - b - c + a) * ux * uy;
}
function fbm(x, y, octaves = 5) {
  let v = 0, amp = 0.5, freq = 1, max = 0;
  for (let i = 0; i < octaves; i++) {
    v += smoothNoise(x * freq, y * freq) * amp;
    max += amp; amp *= 0.5; freq *= 2;
  }
  return v / max;
}

function TopoMesh({ mouseRef }) {
  const meshRef = useRef();
  const geo = useMemo(() => {
    const g = new THREE.PlaneGeometry(14, 14, 120, 120);
    g.rotateX(-Math.PI / 2);
    const pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i);
      const z = pos.getZ(i);
      const h = fbm(x * 0.18 + 2.5, z * 0.18 + 1.3) * 2.6;
      pos.setY(i, h);
    }
    g.computeVertexNormals();
    return g;
  }, []);

  useFrame(() => {
    if (!meshRef.current) return;
    const mx = mouseRef.current.x * 0.18;
    const my = mouseRef.current.y * 0.1;
    meshRef.current.rotation.x = -0.35 + my;
    meshRef.current.rotation.y = mx;
  });

  return (
    <mesh ref={meshRef} geometry={geo} position={[0, -1.2, 0]}>
      <meshStandardMaterial
        color="#f0f0f2"
        roughness={0.9}
        metalness={0.0}
        wireframe={false}
      />
    </mesh>
  );
}

export default function TopographyBackground() {
  const mouseRef = useRef({ x: 0, y: 0 });

  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 0 }}
      onMouseMove={(e) => {
        mouseRef.current.x = (e.clientX / window.innerWidth - 0.5) * 2;
        mouseRef.current.y = (e.clientY / window.innerHeight - 0.5) * 2;
      }}
    >
      <Canvas
        camera={{ position: [0, 6, 10], fov: 45 }}
        gl={{ antialias: true, alpha: true }}
        style={{ background: "transparent" }}
      >
        <ambientLight intensity={1.2} />
        <directionalLight position={[5, 10, 5]} intensity={0.8} color="#ffffff" />
        <directionalLight position={[-5, 8, -3]} intensity={0.3} color="#e8eaf0" />
        <TopoMesh mouseRef={mouseRef} />
      </Canvas>
    </div>
  );
}

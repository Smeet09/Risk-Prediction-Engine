import { useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

/* Floating terrain mesh for Susceptibility card */
function TerrainMesh({ hovered }) {
  const ref = useRef();
  useFrame((state) => {
    ref.current.rotation.y += 0.003;
    ref.current.rotation.x = THREE.MathUtils.lerp(
      ref.current.rotation.x,
      hovered ? -0.35 : -0.5,
      0.06
    );
    ref.current.scale.setScalar(
      THREE.MathUtils.lerp(ref.current.scale.x, hovered ? 1.12 : 1.0, 0.07)
    );
  });
  return (
    <mesh ref={ref}>
      <coneGeometry args={[1.4, 2.2, 6, 8]} />
      <meshStandardMaterial color="#e8e8ea" roughness={0.85} metalness={0.0} flatShading />
    </mesh>
  );
}

export function TerrainCard({ hovered }) {
  return (
    <Canvas
      camera={{ position: [0, 0, 4.5], fov: 40 }}
      gl={{ antialias: true, alpha: true }}
      style={{ width: "100%", height: "100%" }}
    >
      <ambientLight intensity={1.4} />
      <directionalLight position={[3, 5, 3]} intensity={0.9} />
      <pointLight position={[-3, -2, 2]} intensity={0.3} color="#c8d0ff" />
      <TerrainMesh hovered={hovered} />
    </Canvas>
  );
}

/* Glass sphere with rain particles for Rainfall card */
function RainParticles({ count = 300 }) {
  const ref = useRef();
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    positions[i * 3] = (Math.random() - 0.5) * 3;
    positions[i * 3 + 1] = (Math.random() - 0.5) * 3;
    positions[i * 3 + 2] = (Math.random() - 0.5) * 3;
  }
  useFrame(() => {
    if (!ref.current) return;
    ref.current.rotation.y += 0.004;
    const pos = ref.current.geometry.attributes.position;
    for (let i = 0; i < count; i++) {
      pos.setY(i, pos.getY(i) - 0.015);
      if (pos.getY(i) < -1.5) pos.setY(i, 1.5);
    }
    pos.needsUpdate = true;
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return (
    <points ref={ref} geometry={geo}>
      <pointsMaterial color="#aac8ff" size={0.03} transparent opacity={0.7} />
    </points>
  );
}

function GlassSphere({ hovered }) {
  const ref = useRef();
  useFrame((state) => {
    ref.current.rotation.y += 0.005;
    ref.current.scale.setScalar(
      THREE.MathUtils.lerp(ref.current.scale.x, hovered ? 1.1 : 1.0, 0.07)
    );
  });
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[1.1, 64, 64]} />
      <meshPhysicalMaterial
        color="#f0f4ff"
        roughness={0.0}
        metalness={0.0}
        transmission={0.9}
        thickness={0.5}
        transparent
        opacity={0.4}
      />
    </mesh>
  );
}

export function RainSphereCard({ hovered }) {
  return (
    <Canvas
      camera={{ position: [0, 0, 4], fov: 40 }}
      gl={{ antialias: true, alpha: true }}
      style={{ width: "100%", height: "100%" }}
    >
      <ambientLight intensity={1.5} />
      <directionalLight position={[3, 5, 3]} intensity={0.8} color="#d0e0ff" />
      <GlassSphere hovered={hovered} />
      <RainParticles />
    </Canvas>
  );
}

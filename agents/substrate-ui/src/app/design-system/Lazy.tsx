"use client";

import dynamic from "next/dynamic";

/** Client-only: the theme context is not provided during server rendering. */
export const DesignSystemGallery = dynamic(() => import("./Gallery").then((m) => m.DesignSystemGallery), { ssr: false });

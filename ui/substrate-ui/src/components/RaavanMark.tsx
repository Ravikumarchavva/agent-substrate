import type { SVGProps } from "react";

export function RaavanMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      {...props}
    >
      {/* Left flame — tallest */}
      <path d="M6 2C7 5 11 12 10 21C8.5 23 4 23 3 21C2 12 4.5 5 6 2Z" />
      {/* Middle flame */}
      <path d="M14.5 6C15.5 8.5 18.5 14.5 17.5 21C16 23 12 23 11 21C10 14.5 13 8.5 14.5 6Z" />
      {/* Right flame — shortest */}
      <path d="M21 10C21.8 12.5 23.5 17 22.5 21C21.5 23 19.5 23 18.5 21C17.5 17 19.5 12.5 21 10Z" />
    </svg>
  );
}
import type { SVGProps } from "react";

export function SidebarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" aria-hidden="true" {...props}>
      <rect x="3.75" y="4.5" width="16.5" height="15" rx="2.25" />
      <path strokeLinecap="round" d="M9.75 4.5v15" />
    </svg>
  );
}

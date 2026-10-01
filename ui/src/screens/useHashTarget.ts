import { useEffect } from "react";
import { useLocation } from "react-router-dom";

export function useHashTarget(data: unknown) {
  const { hash } = useLocation();
  let target = "";
  try { target = decodeURIComponent(hash.slice(1)); } catch { /* Malformed links have no target. */ }
  useEffect(() => {
    if (!target || !data) return;
    const element = document.getElementById(target);
    element?.scrollIntoView?.({ block: "center" });
    element?.focus({ preventScroll: true });
  }, [target, data]);
  return target;
}

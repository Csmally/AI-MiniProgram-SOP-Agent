import { useState, useEffect, useRef } from 'react';

export default function TypewriterText({ text, speed = 20, enabled = true }) {
  const [displayed, setDisplayed] = useState('');
  const [done, setDone] = useState(false);
  const indexRef = useRef(0);

  useEffect(() => {
    if (!enabled || !text) { setDisplayed(text || ''); setDone(true); return; }

    indexRef.current = 0;
    setDisplayed('');
    setDone(false);

    const timer = setInterval(() => {
      indexRef.current += 1;
      setDisplayed(text.slice(0, indexRef.current));
      if (indexRef.current >= text.length) {
        clearInterval(timer);
        setDone(true);
      }
    }, speed);

    return () => clearInterval(timer);
  }, [text, speed, enabled]);

  if (!enabled || done) return text;

  return <>{displayed}<span className="cursor-blink">|</span></>;
}

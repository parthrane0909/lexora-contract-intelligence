/** @type {import('tailwindcss').Config} */

// Premium legal design system (Phase 9): warm cream paper, charcoal ink,
// deep burgundy accents and restrained gold detailing. These values are the
// single source of truth — globals.css consumes the same ramp through its
// `--brand-*` custom properties so hand-written component CSS and Tailwind
// utilities can never drift apart.
const burgundy = {
  50: "#fbf1f0",
  100: "#f4dedf",
  200: "#e9bfc2",
  300: "#d5949b",
  400: "#bb636e",
  500: "#a13b4a",
  600: "#8a2333",
  700: "#701826",
  800: "#5c1220",
  900: "#420c17",
};

module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: burgundy,
        burgundy,
        gold: {
          50: "#fbf6e9",
          100: "#f4ead0",
          200: "#e9d7a6",
          300: "#dcc077",
          400: "#c9a551",
          500: "#b08d3f",
          600: "#96742f",
          700: "#7a5c25",
          800: "#5e461d",
        },
        cream: {
          DEFAULT: "#f5f0e6",
          50: "#fdfbf6",
          100: "#f8f4ec",
          200: "#f0e9db",
          300: "#e5dbc7",
          400: "#d7c9ad",
        },
        paper: "#fffdf8",
        ink: {
          DEFAULT: "#1c1917",
          soft: "#4b463f",
          mute: "#78716c",
        },
        line: {
          DEFAULT: "#e7e0d2",
          soft: "#efe9dd",
          strong: "#d9d0be",
        },
      },
      fontFamily: {
        serif: ['Georgia', '"Times New Roman"', "serif"],
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          '"Segoe UI"',
          "Roboto",
          '"Helvetica Neue"',
          "Arial",
          "sans-serif",
        ],
      },
      letterSpacing: {
        eyebrow: "0.16em",
      },
      boxShadow: {
        card: "0 1px 2px rgba(28,25,23,.04)",
        lift: "0 18px 40px -28px rgba(28,25,23,.45)",
        popover: "0 24px 48px -24px rgba(28,25,23,.35)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "scale-in": {
          "0%": { opacity: "0", transform: "translateY(10px) scale(.985)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
      animation: {
        "fade-up": "fade-up .55s cubic-bezier(.22,.61,.36,1) both",
        "fade-in": "fade-in .4s ease both",
        "scale-in": "scale-in .22s cubic-bezier(.22,.61,.36,1) both",
        shimmer: "shimmer 1.4s linear infinite",
      },
    },
  },
  plugins: [],
};

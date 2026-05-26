/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./main/template/**/*.html"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      colors: {
        ink: {
          950: "#070809",
          900: "#0b0c0e",
          800: "#15171a",
          700: "#1f2227",
        },
        brand: {
          50:  "#f3f0ff",
          100: "#e9e2ff",
          200: "#d5c9ff",
          300: "#b9a6ff",
          400: "#9d7dfe",
          500: "#7c5cfc",
          600: "#6643e8",
          700: "#5232cc",
          800: "#3f24a2",
          900: "#2e1875",
        },
      },
    },
  },
  plugins: [],
};

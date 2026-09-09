/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./Located Folder/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        ink: "#173774",
        pine: "#294d98",
        mint: "#bcd0ff",
        cream: "#f4f7fc",
        coral: "#d75555"
      },
      fontFamily: {
        sans: ["Poppins", "sans-serif"],
        serif: ["Poppins", "sans-serif"],
        mono: ["Roboto Mono", "monospace"]
      },
      boxShadow: {
        soft: "0 18px 55px rgba(23, 55, 116, 0.14)"
      }
    }
  },
  plugins: []
};

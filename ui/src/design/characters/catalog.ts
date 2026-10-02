export const avatarColors = [
  { id: "slate", name: "Slate", color: "#bcbfd0", light: "#c1c4d3" },
  { id: "sky", name: "Sky", color: "#60aaf5", light: "#68aff8" },
  { id: "sun", name: "Sun", color: "#f2d153", light: "#f5d65e" },
  { id: "orchid", name: "Orchid", color: "#c96bd8", light: "#d074dd" },
  { id: "lime", name: "Lime", color: "#b9d73f", light: "#c0dc4a" },
  { id: "rose", name: "Rose", color: "#e373a8", light: "#e77db0" },
  { id: "coral", name: "Coral", color: "#eb856b", light: "#ef8f77" },
  { id: "jade", name: "Jade", color: "#5db69f", light: "#68bda7" },
  { id: "indigo", name: "Indigo", color: "#5e70fa", light: "#697bff" },
  { id: "violet", name: "Violet", color: "#9459f7", light: "#9f66fc" },
  { id: "teal", name: "Teal", color: "#5bafbb", light: "#88cbd0" },
  { id: "sand", name: "Sand", color: "#ceb38b", light: "#e4cfab" },
] as const;

export const characters = [
  { id: "gumdrop", name: "Mint gumdrop" }, { id: "mochi", name: "Peach mochi" },
  { id: "cloud", name: "Lilac cloud" }, { id: "star", name: "Lemon star" },
  { id: "pebble", name: "Teal pebble" }, { id: "tomato", name: "Tomato" },
  { id: "bean", name: "Navy bean" }, { id: "dune", name: "Sand dune" },
  { id: "jelly", name: "Coral jellybean" }, { id: "cactus", name: "Sage cactus" },
] as const;

export const pets = [
  { id: "cat", name: "Cat" }, { id: "fox", name: "Fox" },
  { id: "hamster", name: "Hamster" }, { id: "penguin", name: "Penguin" },
  { id: "snail", name: "Snail" }, { id: "ghost", name: "Ghost" },
  { id: "mushroom", name: "Mushroom" }, { id: "bat", name: "Bat" },
] as const;

export type CharacterId = typeof characters[number]["id"];
export type PetId = typeof pets[number]["id"];

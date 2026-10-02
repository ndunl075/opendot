export const avatarColors = [
  { id: "slate", name: "Slate", color: "#a0acc6", light: "#b9c3d8" },
  { id: "sky", name: "Sky", color: "#69ace8", light: "#8bc4ef" },
  { id: "sun", name: "Sun", color: "#e9c750", light: "#f4dc7b" },
  { id: "orchid", name: "Orchid", color: "#bd7dd0", light: "#d49de0" },
  { id: "lime", name: "Lime", color: "#afc956", light: "#c9dc7c" },
  { id: "rose", name: "Rose", color: "#da84aa", light: "#e9a4c2" },
  { id: "coral", name: "Coral", color: "#e48b74", light: "#f1ab95" },
  { id: "jade", name: "Jade", color: "#62b49a", light: "#8bceb4" },
  { id: "indigo", name: "Indigo", color: "#7b87db", light: "#a0abeb" },
  { id: "violet", name: "Violet", color: "#a17cda", light: "#bfa0ed" },
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

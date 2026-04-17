# Frontend Dockerfile
FROM node:20-alpine

WORKDIR /app

# Copia los archivos de configuración
COPY package*.json ./

# Instala todas las dependencias
RUN npm install

# Copia el proyecto
COPY . .

# Exponemos el puerto de desarrollo de Vite
EXPOSE 5173

# Corremos Vite en modo dev expuesto para que se pueda acceder desde el navegador localmente
CMD ["npm", "run", "dev"]

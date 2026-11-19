# Packaging and Sharing Docker Images to Production Server

This guide explains how to build, package, and transfer the **SourceBuddy** Docker image from your local development machine directly to the production server team at **`164.52.192.57`** without using a public online registry.

---

## Method 1: The Offline Tarball Archive (Recommended)
This method is perfect for secure deployments. You package the built image into a single compressed `.tar` file, send it via any file transfer system (SFTP, Google Drive, Shared Folder), and the server team imports it directly.

### Step 1: Build the Image locally on your PC
Open your terminal (PowerShell or Command Prompt) on your local PC, navigate to the `/AI_Vector_Bom_Sourcing` folder, and build the GPU-ready production image:
```bash
docker build -f AI_Sourcing_API/Dockerfile.gpu -t sourcebuddy-app:latest .
```
> [!NOTE]
> *Yes, you can build this on your Windows PC even if you don't have a GPU locally!* Docker will download and bundle the Linux CUDA/PyTorch libraries inside the image, making it fully ready to execute on the server's GPU.

### Step 2: Save the Image to a Archive File
Run the `docker save` command to compress the image into a `.tar` archive file on your hard drive:
```bash
docker save -o sourcebuddy-app-latest.tar sourcebuddy-app:latest
```
*(This will generate a file named `sourcebuddy-app-latest.tar` of about 2GB - 3GB in your current directory because it includes PyTorch and CUDA).*

### Step 3: Send the Tarball to the Server Team
Upload `sourcebuddy-app-latest.tar` to your team's file share (SFTP, Google Drive, OneDrive, or a shared corporate drive) and give them the download link.

### Step 4: Server Team Loads the Image on the Server
Once the server team copies the file to the target server `164.52.192.57`, they simply run:
```bash
docker load -i sourcebuddy-app-latest.tar
```
*(This imports the image back into Docker on the server, ready to run).*

---

## Method 2: The Docker Registry Method (Cloud Push/Pull)
If your team uses a Docker registry (like Docker Hub, AWS ECR, or a private registry), you can publish it online and let the server team pull it.

### Step 1: Log in to Docker Registry on your PC
```bash
docker login
```

### Step 2: Tag your Local Image
```bash
# Replace 'your-username' with your actual registry username
docker tag sourcebuddy-app:latest your-username/sourcebuddy-app:latest
```

### Step 3: Push the Image to the Cloud
```bash
docker push your-username/sourcebuddy-app:latest
```

### Step 4: Server Team Pulls the Image
On the server `164.52.192.57`, the server team logs in and runs:
```bash
docker pull your-username/sourcebuddy-app:latest
```

---

## How to Run the Imported Image via Docker Compose
When the server team runs the stack using `docker-compose.prod.yml`, they no longer need to build the image from source. They just pull/load the image and run it. 

We can optimize **`docker-compose.prod.yml`** to use the loaded pre-built image instead of building it from context:
```yaml
  sourcebuddy-app:
    image: sourcebuddy-app:latest
    container_name: sourcebuddy-app
    ...
```
*(This is cleaner for production and prevents compilation/build delays on the server!)*

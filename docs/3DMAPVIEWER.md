# 3DMapViewer Module

The `3dmapviewer` plugin displays a simple 3D terrain view.

```
module load 3dmapviewer
3dmap start
```

It retrieves map tiles from the configured map service and
combines them with SRTM elevation data to build a textured mesh.
The vehicle path is drawn in real time using GPS updates.

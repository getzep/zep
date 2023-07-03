## Setting up Zep in a Kubernetes Cluster:

#### 1. Setup a Kubernetes Cluster (e.g., using Docker Desktop for local testing on your laptop)
Make sure cluster is running...
>> kubectl cluster-info

#### 2. Create a Secret to store the OPENAI_API_KEY
>> kubectl create secret generic zep-secret --from-literal=ZEP_OPENAI_API_KEY=<your-api-key>

#### 3. Run the deployment yaml
>> kubectl apply -f zep-deployment.yaml

#### 4. Make sure you have setup port forwarding (default listen port in the config is localhost:8000)
>> kubectl port-forward service/zep 8000:8000

#### Notes:
The base instructions above will bring up a Kubernetes deployment of Zep server and related containers,
with the default configuration. Zep has a number of configuration params that are provided in the .env file to setup AUTH etc., for users wanting to customize deployments and have fine grained controls over optional
features. If you want to use any of those optional params, you will need to create a configMap from those
params and update the zep-deployment.yaml file to reference/load those params. 
You can run: kubectl apply -f zep-deployment.yaml to reload the cluster with new config params. 

# Replaced by immutable digest references by build_profiles.py before building.
ARG NODE_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE}
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    python -m pip install --no-cache-dir numpy==2.2.2 pandas==2.2.3 scipy==1.15.1 scikit-learn==1.6.1 joblib==1.4.2 threadpoolctl==3.5.0 python-dateutil==2.9.0.post0 six==1.17.0 pytz==2025.1 tzdata==2025.1 && \
    npm install --global typescript@5.7.3 && \
    mkdir -p /opt/mm /input /work && \
    cd /opt/mm && npm install --save-exact @types/node@22.10.2 react@19.0.0 react-dom@19.0.0 jsdom@26.0.0 && \
    chmod 755 /input
COPY worker_main.py sqlite_main.py /opt/mm/
ENV NODE_PATH=/opt/mm/node_modules
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["python", "-I", "/opt/mm/worker_main.py"]

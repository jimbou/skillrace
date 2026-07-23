FROM skillrace/skillgen-base:0.73.1-construction
WORKDIR /workspace
RUN python3 -c "open('big.csv','w').write('i,n\n'+''.join(f'{k},{k}\n' for k in range(1,101)))"
